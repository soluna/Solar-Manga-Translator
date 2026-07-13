from __future__ import annotations

import copy
import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any


REGION_ORIGINS = frozenset({"automatic", "user", "derived"})


def region_origin(region: Mapping[str, Any]) -> str:
    explicit = str(region.get("origin") or "").strip().lower()
    if explicit in REGION_ORIGINS:
        return explicit
    kind = str(region.get("kind") or "auto").strip().lower()
    if kind == "merged":
        return "derived"
    if kind == "manual":
        return "user"
    return "automatic"


def is_user_authored_region(region: Mapping[str, Any]) -> bool:
    return region_origin(region) in {"user", "derived"}


class PageRegionCollection:
    """Build the canonical ordered text-region collection for one page.

    A materialized editable cache is authoritative when it exists. When that
    derived cache is unavailable, persisted automatic regions are retained and
    the current user-authored collection replaces persisted user-authored
    regions. This makes an empty authored collection an explicit deletion, not
    an ambiguous fallback signal.
    """

    @classmethod
    def reconcile(
        cls,
        *,
        page_id: str,
        persisted_regions: Iterable[Mapping[str, Any]] | None,
        materialized_regions: Iterable[Mapping[str, Any]] | None,
        authored_regions: Iterable[Mapping[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if materialized_regions is not None:
            candidates = materialized_regions
        else:
            persisted_automatic = (
                region
                for region in (persisted_regions or [])
                if isinstance(region, Mapping) and not is_user_authored_region(region)
            )
            candidates = [*persisted_automatic, *(authored_regions or [])]

        reconciled: list[dict[str, Any]] = []
        seen_region_ids: set[str] = set()
        for region in candidates:
            if not isinstance(region, Mapping):
                continue
            region_id = str(region.get("region_id") or region.get("id") or "").strip()
            if not region_id or region_id in seen_region_ids:
                continue
            normalized = copy.deepcopy(dict(region))
            normalized["region_id"] = region_id
            normalized["page_id"] = page_id
            normalized["kind"] = str(normalized.get("kind") or "auto").strip().lower() or "auto"
            normalized["origin"] = region_origin(normalized)
            reconciled.append(normalized)
            seen_region_ids.add(region_id)
        return reconciled

    @classmethod
    def recommend_font_size(
        cls,
        *,
        bbox: Iterable[int | float],
        regions: Iterable[Mapping[str, Any]] | None,
        direction: str = "auto",
    ) -> int:
        coordinates = [int(round(float(value))) for value in bbox]
        if len(coordinates) != 4:
            raise ValueError("A text region bbox must contain four coordinates")
        x1, y1, x2, y2 = coordinates
        short_side = max(8, min(abs(x2 - x1), abs(y2 - y1)))
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
            distance = math.hypot(((rx1 + rx2) / 2.0) - center_x, ((ry1 + ry2) / 2.0) - center_y)
            candidates.append((region_direction == normalized_direction, distance, font_size))

        same_direction = sorted((item for item in candidates if item[0]), key=lambda item: item[1])
        nearest = same_direction[:3] or sorted(candidates, key=lambda item: item[1])[:5]
        if nearest:
            recommended = int(round(statistics.median(item[2] for item in nearest)))
        else:
            recommended = int(round(short_side * 0.22))
            recommended = max(14, min(48, recommended))
        return max(8, min(short_side, recommended))

    @staticmethod
    def _normalized_direction(direction: str, bbox: list[int | float]) -> str:
        normalized = str(direction or "").strip().lower()
        if normalized.startswith("v"):
            return "vertical"
        if normalized.startswith("h"):
            return "horizontal"
        x1, y1, x2, y2 = bbox
        return "vertical" if abs(y2 - y1) > abs(x2 - x1) * 1.15 else "horizontal"
