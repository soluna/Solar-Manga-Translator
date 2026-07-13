from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from domain.glossary_candidates import GlossaryCandidate, GlossaryCandidateDiscovery


class GlossaryCandidateDiscoveryTests(unittest.TestCase):
    def test_discovers_explicit_names_without_absorbing_the_sentence_before_an_honorific(self) -> None:
        context = "\n".join([
            "1. [page-1.png] 私の名前は片桐 奈々美",
            "2. [page-1.png] ど…どうしたの？奈々美ちゃん",
            "3. [page-2.png] 今日はどうしたの奈々美ちゃん",
            "4. [page-2.png] もちろんヒロくんにも秘密です",
            "5. [page-3.png] ごめんね…ヒロ君…",
            "6. [page-3.png] こんなにたくさんの男の人達が…",
            "7. [page-4.png] ハァハァななみちゃーん！",
        ])

        sources = {
            candidate.source
            for candidate in GlossaryCandidateDiscovery.discover(context)
        }

        self.assertTrue({"片桐 奈々美", "片桐", "奈々美", "ヒロ", "ななみ"}.issubset(sources))
        self.assertNotIn("今日はどうしたの奈々美", sources)
        self.assertNotIn("もちろんヒロ", sources)
        self.assertNotIn("たく", sources)

    def test_model_selected_full_name_covers_detector_name_fragments(self) -> None:
        candidates = [
            GlossaryCandidate("片桐 奈々美", "self_introduction", ("私の名前は片桐 奈々美",)),
            GlossaryCandidate("片桐", "self_introduction_part", ("私の名前は片桐 奈々美",)),
            GlossaryCandidate("奈々美", "honorific", ("奈々美ちゃん",)),
        ]

        missing = GlossaryCandidateDiscovery.missing_candidates(
            candidates,
            [{"source": "片桐 奈々美", "translation": "片桐奈奈美"}],
        )

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
