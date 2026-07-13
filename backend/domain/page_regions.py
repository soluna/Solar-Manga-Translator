from __future__ import annotations

import copy
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
