from __future__ import annotations

from pathlib import Path
from typing import Any


CORE_MODELS = (
    {
        "id": "detector-default",
        "stage": "detect",
        "label": "默认文本检测模型",
        "relative_path": "detection/detect-20241225.ckpt",
        "sha256": "67ce1c4ed4793860f038c71189ba9630a7756f7683b1ee5afb69ca0687dc502e",
    },
    {
        "id": "ocr-48px",
        "stage": "recognize",
        "label": "48px OCR 模型",
        "relative_path": "ocr/ocr_ar_48px.ckpt",
        "sha256": "29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d",
    },
    {
        "id": "ocr-alphabet",
        "stage": "recognize",
        "label": "OCR 字符表",
        "relative_path": "ocr/alphabet-all-v7.txt",
        "sha256": "f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a",
    },
    {
        "id": "inpainter-lama-large",
        "stage": "inpaint",
        "label": "LaMa 去字模型",
        "relative_path": "inpainting/lama_large_512px.ckpt",
        "sha256": "11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935",
    },
)


def build_model_readiness(models_dir: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for model in CORE_MODELS:
        path = models_dir / str(model["relative_path"])
        partial_path = path.with_suffix(f"{path.suffix}.part")
        size_bytes = path.stat().st_size if path.exists() else 0
        items.append(
            {
                **model,
                "path": str(path),
                "present": bool(path.is_file() and size_bytes > 0),
                "size_bytes": size_bytes,
                "partial": partial_path.exists(),
                "partial_size_bytes": partial_path.stat().st_size if partial_path.exists() else 0,
            }
        )

    missing = [item for item in items if not item["present"]]
    return {
        "status": "ready" if not missing else "download_required",
        "models_dir": str(models_dir),
        "ready_count": len(items) - len(missing),
        "total_count": len(items),
        "missing_ids": [str(item["id"]) for item in missing],
        "items": items,
    }
