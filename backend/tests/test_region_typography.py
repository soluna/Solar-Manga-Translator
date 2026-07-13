from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.region_typography import RegionTypography


class RegionTypographyTests(unittest.TestCase):
    def test_recommendation_inherits_nearby_same_direction_typography(self) -> None:
        recommendation = RegionTypography.recommend_font_size(
            bbox=[60, 60, 105, 105],
            direction="horizontal",
            regions=[
                {
                    "bbox": [10, 10, 55, 55],
                    "direction": "horizontal",
                    "style": {"font_size": 18},
                    "flags": {},
                },
                {
                    "bbox": [10, 80, 40, 150],
                    "direction": "vertical",
                    "style": {"font_size": 40},
                    "flags": {},
                },
            ],
        )

        self.assertEqual(recommendation, 18)

    def test_ocr_estimate_is_bounded_by_stable_baseline_and_retry_is_idempotent(self) -> None:
        first = RegionTypography.resolve_ocr_font_size(
            bbox=[0, 0, 120, 160],
            recommended_font_size=18,
            current_font_size=18,
            ocr_font_size=9999,
        )
        second = RegionTypography.resolve_ocr_font_size(
            bbox=[0, 0, 120, 160],
            recommended_font_size=18,
            current_font_size=first,
            ocr_font_size=9999,
        )

        self.assertEqual(first, 32)
        self.assertEqual(second, first)

    def test_missing_ocr_estimate_preserves_current_font_size(self) -> None:
        self.assertEqual(
            RegionTypography.resolve_ocr_font_size(
                bbox=[0, 0, 80, 120],
                recommended_font_size=20,
                current_font_size=24,
                ocr_font_size=None,
            ),
            24,
        )


if __name__ == "__main__":
    unittest.main()
