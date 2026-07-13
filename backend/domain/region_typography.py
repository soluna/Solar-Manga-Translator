from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any


class RegionTypography:
    """Resolve stable typography defaults for a Text Region.

    Recognition estimates are treated as evidence, not as an unconditional
    replacement for the page's existing typography. The creation-time
    recommendation is the stable baseline so retrying OCR is idempotent.
    """

    @classmethod
    def recommend_font_size(
        cls,
        *,
        bbox: Iterable[int | float],
        regions: Iterable[Mapping[str, Any]] | None,
        direction: str = "auto",
    ) -> int:
        coordinates = cls._coordinates(bbox)
        x1, y1, x2, y2 = coordinates
        short_side = cls._short_side(coordinates)
        center_x = (x1 + x2) / 2.0
        center_y = (y1 + y2) / 2.0
        normalized_direction = cls._normalized_direction(direction, coordinates)

        candidates: list[tuple[bool, float, float]] = []
        for region in regions or []:
            if not isinstance(region, Mapping):
                continue
            if bool((region.get("flags") or {}).get("disabled")):
                continue
            region_bbox = region.get("bbox")
            if not isinstance(region_bbox, (list, tuple)) or len(region_bbox) != 4:
                continue
            try:
                rx1, ry1, rx2, ry2 = [float(value) for value in region_bbox]
                font_size = float((region.get("style") or {}).get("font_size"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(font_size) or font_size < 8:
                continue
            region_direction = cls._normalized_direction(
                str(region.get("direction") or "auto"),
                [rx1, ry1, rx2, ry2],
            )
            distance = math.hypot(
                ((rx1 + rx2) / 2.0) - center_x,
                ((ry1 + ry2) / 2.0) - center_y,
            )
            candidates.append((region_direction == normalized_direction, distance, font_size))

        same_direction = sorted((item for item in candidates if item[0]), key=lambda item: item[1])
        nearest = same_direction[:3] or sorted(candidates, key=lambda item: item[1])[:5]
        if nearest:
            recommended = int(round(statistics.median(item[2] for item in nearest)))
        else:
            recommended = int(round(short_side * 0.22))
            recommended = max(14, min(48, recommended))
        return max(8, min(short_side, recommended))

    @classmethod
    def resolve_ocr_font_size(
        cls,
        *,
        bbox: Iterable[int | float],
        recommended_font_size: Any,
        current_font_size: Any,
        ocr_font_size: Any,
    ) -> int:
        coordinates = cls._coordinates(bbox)
        short_side = cls._short_side(coordinates)
        baseline = cls._finite_font_size(recommended_font_size)
        if baseline is None:
            baseline = cls._finite_font_size(current_font_size)
        if baseline is None:
            baseline = cls.recommend_font_size(bbox=coordinates, regions=[])
        baseline = max(8, min(short_side, int(round(baseline))))

        current = cls._finite_font_size(current_font_size)
        if current is None:
            current = baseline
        current = max(8, min(short_side, int(round(current))))

        estimate = cls._finite_font_size(ocr_font_size)
        if estimate is None:
            return current

        lower_bound = max(8, int(round(baseline * 0.6)))
        upper_bound = min(short_side, max(baseline, int(round(baseline * 1.75))))
        lower_bound = min(lower_bound, upper_bound)
        return max(lower_bound, min(upper_bound, int(round(estimate))))

    @staticmethod
    def _finite_font_size(value: Any) -> float | None:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized) or normalized <= 0:
            return None
        return normalized

    @staticmethod
    def _coordinates(bbox: Iterable[int | float]) -> list[int]:
        coordinates = [int(round(float(value))) for value in bbox]
        if len(coordinates) != 4:
            raise ValueError("A text region bbox must contain four coordinates")
        return coordinates

    @staticmethod
    def _short_side(bbox: list[int | float]) -> int:
        x1, y1, x2, y2 = bbox
        return max(8, int(round(min(abs(x2 - x1), abs(y2 - y1)))))

    @staticmethod
    def _normalized_direction(direction: str, bbox: list[int | float]) -> str:
        normalized = str(direction or "").strip().lower()
        if normalized.startswith("v"):
            return "vertical"
        if normalized.startswith("h"):
            return "horizontal"
        x1, y1, x2, y2 = bbox
        return "vertical" if abs(y2 - y1) > abs(x2 - x1) * 1.15 else "horizontal"
