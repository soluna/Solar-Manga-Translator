#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from engine.translator import TranslatorEngine  # noqa: E402
from system_fonts import BUNDLED_DEFAULT_FONT_NAME, find_default_bundled_font  # noqa: E402


PROJECT_ID = "canvas-e2e-fixture"
PROJECT_TITLE = "Canvas E2E Fixture"
PROJECT_NOTE = "本地画布交互回归夹具：用于验证选框、拖拽、缩放、方向键微调和结果对照。"
PAGE_SIZE = (1280, 1820)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def pick_font() -> tuple[str, str]:
    path = find_default_bundled_font(BACKEND_DIR)
    return (f"system:{BUNDLED_DEFAULT_FONT_NAME}", str(path)) if path is not None else ("", "")


def safe_remove(path: Path) -> None:
    if path.exists():
        import shutil

        shutil.rmtree(path, ignore_errors=True)


def draw_page(image_path: Path, translated_path: Path, page_index: int, regions: list[dict[str, Any]]) -> None:
    width, height = PAGE_SIZE
    source = Image.new("RGB", (width, height), "#fcfbf7")
    draw = ImageDraw.Draw(source)
    translated = Image.new("RGB", (width, height), "#ffffff")
    translated_draw = ImageDraw.Draw(translated)

    header_color = "#2a2018"
    accent = "#d86c2a"
    panel_fill = "#ffffff"
    bubble_outline = "#1a140f"

    # Background panels.
    draw.rounded_rectangle((60, 80, 1220, 620), radius=26, outline="#d4c6b2", width=4, fill="#fffdfa")
    draw.rounded_rectangle((80, 720, 1200, 1110), radius=24, outline="#d8cdbf", width=4, fill="#fffefc")
    draw.rounded_rectangle((80, 1180, 1200, 1710), radius=24, outline="#d8cdbf", width=4, fill="#fffefc")
    draw.line((640, 80, 640, 620), fill="#e4dacb", width=3)
    draw.line((80, 1110, 1200, 1110), fill="#e4dacb", width=3)
    draw.rectangle((890, 190, 1090, 420), outline="#29201a", width=6)
    draw.rectangle((920, 220, 1060, 390), outline=accent, width=10)
    draw.rectangle((150, 790, 310, 980), outline="#342820", width=5)
    draw.rectangle((970, 790, 1070, 980), outline="#342820", width=5)
    draw.rectangle((440, 1270, 870, 1680), outline="#2c2118", width=7)
    draw.ellipse((505, 1340, 805, 1645), outline="#111111", width=6)
    draw.line((650, 1320, 650, 1680), fill="#1f1a16", width=4)
    draw.line((520, 1490, 790, 1490), fill="#1f1a16", width=4)

    draw.text((96, 106), f"Canvas Fixture · Page {page_index}", fill=header_color)
    draw.text((96, 145), "用于本地回归测试：拖框 / 缩框 / 微调 / 结果对照", fill="#7f6652")

    for index, region in enumerate(regions, start=1):
        x1, y1, x2, y2 = region["bbox"]
        fill = region.get("bubble_fill", panel_fill)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=28, outline=bubble_outline, width=4, fill=fill)
        draw.text((x1 + 12, y1 - 30), f"#{index}", fill=accent)

        translated_draw.rounded_rectangle((x1, y1, x2, y2), radius=28, outline=bubble_outline, width=4, fill=fill)
        translated_draw.text((x1 + 16, y1 + 16), region["translation"]["resolved"], fill="#111111")

    source.save(image_path, quality=94)
    translated.save(translated_path, quality=94)


def build_region(
    *,
    region_id: str,
    page_id: str,
    bbox: list[int],
    source_text: str,
    resolved_translation: str,
    direction: str = "vertical",
    auto_font_style: str = "gothic",
    font_key_override: str = "",
    font_path: str = "",
    font_size: int = 30,
    alignment: str = "auto",
    bubble_fill: str = "#ffffff",
) -> dict[str, Any]:
    return {
        "region_id": region_id,
        "page_id": page_id,
        "kind": "auto",
        "source_ids": [],
        "bbox": bbox,
        "polygon": None,
        "direction": direction,
        "source_text": source_text,
        "ocr_confidence": 0.997,
        "translation": {
            "machine": resolved_translation,
            "edited": "",
            "resolved": resolved_translation,
        },
        "style": {
            "auto_font_style": auto_font_style,
            "font_style_override": "",
            "font_style": auto_font_style,
            "font_family": Path(font_path).name if font_path else "",
            "font_path": font_path,
            "font_key_override": font_key_override,
            "font_size": font_size,
            "font_size_override": None,
            "letter_spacing": 1.0,
            "line_spacing": 1.0,
            "alignment": alignment,
        },
        "flags": {
            "disabled": False,
            "keep_original": False,
            "translation_enabled": True,
        },
        "audit": {
            "created_by": "fixture",
            "updated_at": now_iso(),
        },
        "bubble_fill": bubble_fill,
    }


def build_page_document(project_id: str, page_id: str, regions: list[dict[str, Any]], document_version: int) -> dict[str, Any]:
    width, height = PAGE_SIZE
    return {
        "page_id": page_id,
        "review_mode": "canvas_beta",
        "source_image": f"/api/pages/{project_id}/{page_id}/source-image",
        "base_image": f"/api/pages/{project_id}/{page_id}/base-image",
        "preview_image": f"/api/pages/{project_id}/{page_id}/preview-image",
        "translated_image": f"/api/pages/{project_id}/{page_id}/translated-image",
        "dimensions": {
            "width": width,
            "height": height,
        },
        "regions": [
            {key: value for key, value in region.items() if key != "bubble_fill"}
            for region in regions
        ],
        "erase_regions": [],
        "metadata": {
            "document_version": document_version,
            "updated_at": now_iso(),
            "revision": 1,
        },
    }


def build_fixture_regions(font_key: str, font_path: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "0001.jpg": [
            build_region(
                region_id="fixture-0001-r1",
                page_id="0001.jpg",
                bbox=[150, 250, 360, 560],
                source_text="けっこう狭いですね。",
                resolved_translation="真的挺窄的。",
                direction="vertical",
                auto_font_style="gothic",
                font_key_override=font_key,
                font_path=font_path,
                font_size=34,
            ),
            build_region(
                region_id="fixture-0001-r2",
                page_id="0001.jpg",
                bbox=[890, 235, 1090, 490],
                source_text="本日はご乗車いただきありがとうございます。",
                resolved_translation="感谢今天搭乘本线路。",
                direction="vertical",
                auto_font_style="mincho",
                font_key_override=font_key,
                font_path=font_path,
                font_size=30,
            ),
            build_region(
                region_id="fixture-0001-r3",
                page_id="0001.jpg",
                bbox=[965, 760, 1088, 1000],
                source_text="SPIRITS COMICS",
                resolved_translation="SPIRITS COMICS",
                direction="horizontal",
                auto_font_style="gothic",
                font_key_override=font_key,
                font_path=font_path,
                font_size=24,
                alignment="center",
                bubble_fill="#f7f2ea",
            ),
        ],
        "0002.jpg": [
            build_region(
                region_id="fixture-0002-r1",
                page_id="0002.jpg",
                bbox=[120, 260, 380, 520],
                source_text="どこから来られたんですか？",
                resolved_translation="你是从哪里来的？",
                direction="vertical",
                auto_font_style="gothic",
                font_key_override=font_key,
                font_path=font_path,
                font_size=32,
            ),
            build_region(
                region_id="fixture-0002-r2",
                page_id="0002.jpg",
                bbox=[910, 320, 1140, 560],
                source_text="都内です。",
                resolved_translation="就在东京都内。",
                direction="vertical",
                auto_font_style="round",
                font_key_override=font_key,
                font_path=font_path,
                font_size=28,
            ),
        ],
    }


def create_fixture(project_id: str) -> dict[str, Any]:
    engine = TranslatorEngine(BACKEND_DIR)
    font_key, font_path = pick_font()
    project_dir = engine.projects_root / project_id
    source_dir = engine.output_root / project_id / "source"
    translated_dir = engine.output_root / project_id / "translated"
    rerender_cache_dir = engine._rerender_cache_dir(project_id)
    pages_dir = project_dir / "pages"

    safe_remove(project_dir)
    safe_remove(source_dir.parent)
    safe_remove(rerender_cache_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    translated_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    fixture_regions = build_fixture_regions(font_key, font_path)

    source_images = []
    translated_output_map = {}
    for index, (stored_name, regions) in enumerate(fixture_regions.items(), start=1):
        image_path = source_dir / stored_name
        translated_path = translated_dir / stored_name
        draw_page(image_path, translated_path, index, regions)
        source_images.append(
            {
                "name": f"Fixture Page {index}",
                "stored_name": stored_name,
                "url": f"/output/{project_id}/source/{stored_name}",
            }
        )
        translated_output_map[stored_name] = stored_name
        page_dir = pages_dir / stored_name
        page_dir.mkdir(parents=True, exist_ok=True)
        page_document = build_page_document(project_id, stored_name, regions, engine.PAGE_DOCUMENT_VERSION)
        (page_dir / "page_document.json").write_text(
            json.dumps(page_document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        page_cache_dir = rerender_cache_dir / stored_name
        page_cache_dir.mkdir(parents=True, exist_ok=True)
        Image.open(image_path).save(page_cache_dir / "inpainted.png")
        (page_cache_dir / "meta.json").write_text(
            json.dumps({"base_kind": "inpainted"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    session = {
        "source_dir": str(source_dir),
        "translated_dir": str(translated_dir),
        "source_images": source_images,
        "download_path": "",
        "translated_output_map": translated_output_map,
        "rerender_generation": 0,
        "manual_regions": {},
        "workflow_stage": "translated",
        "mask_debug_dir": "",
        "rerender_cache_dir": str(rerender_cache_dir),
        "last_config": {"rerender_output_format": "jpg", "target_lang": "CHS"},
        "deferred_output_names": set(),
        "translation_region_overrides": {},
        "translation_region_skip_overrides": {},
        "translation_region_disabled_overrides": {},
        "translation_region_layout_overrides": {},
        "style_region_overrides": {},
        "project_id": project_id,
        "project_title": PROJECT_TITLE,
        "project_note": PROJECT_NOTE,
        "review_mode": "canvas_beta",
        "project_created_at": now_iso(),
        "project_updated_at": now_iso(),
    }

    engine.persist_project_state(project_id, session, persist_page_documents=False)
    return {
        "project_id": project_id,
        "title": PROJECT_TITLE,
        "page_count": len(source_images),
        "pages": [item["stored_name"] for item in source_images],
        "font_key": font_key,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic local canvas review fixture project.")
    parser.add_argument("--project-id", default=PROJECT_ID, help="Fixture project id to create/reset.")
    args = parser.parse_args()

    payload = create_fixture(args.project_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
