from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _to_json_compatible(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(item) for item in value]
    return value


def save_rerender_cache(source_path: str, ctx: Any) -> None:
    try:
        cache_root = os.getenv("MT_RERENDER_CACHE_DIR", "").strip()
        if not cache_root:
            return

        page_name = Path(source_path).name
        page_dir = Path(cache_root) / page_name
        page_dir.mkdir(parents=True, exist_ok=True)

        text_regions = getattr(ctx, "text_regions", None) or []
        serialized_regions = []
        for region in text_regions:
            if not hasattr(region, "to_dict"):
                continue
            serialized_regions.append(_to_json_compatible(region.to_dict()))

        gimp_mask = getattr(ctx, "gimp_mask", None)
        img_inpainted = getattr(ctx, "img_inpainted", None)
        has_gimp_mask = isinstance(gimp_mask, np.ndarray) and gimp_mask.ndim == 3 and gimp_mask.shape[2] >= 3

        meta = {
            "page_name": page_name,
            "region_count": len(serialized_regions),
            "has_inpainted": bool(img_inpainted is not None),
            "has_gimp_mask": has_gimp_mask,
        }

        (page_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (page_dir / "regions.json").write_text(
            json.dumps(serialized_regions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if has_gimp_mask:
            cv2.imwrite(
                str(page_dir / "inpainted.png"),
                np.ascontiguousarray(gimp_mask[:, :, :3]),
            )
        elif img_inpainted is not None:
            cv2.imwrite(
                str(page_dir / "inpainted.png"),
                cv2.cvtColor(img_inpainted, cv2.COLOR_RGB2BGR),
            )
    except Exception:
        return
