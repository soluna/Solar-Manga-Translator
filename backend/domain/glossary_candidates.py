from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class GlossaryCandidate:
    source: str
    reason: str
    evidence: tuple[str, ...]


class GlossaryCandidateDiscovery:
    """Find high-confidence proper-noun evidence without inventing translations."""

    MAX_CANDIDATES = 24
    MAX_EVIDENCE_LINES = 24
    MAX_EVIDENCE_LINE_CHARS = 180

    _SELF_INTRODUCTION_PATTERNS = (
        re.compile(
            r"(?:私|わたし|僕|ぼく|俺|おれ|あたし|ワタシ)(?:の)?(?:名前|なまえ)"
            r"\s*(?:は|わ|が|:|：)\s*([^\n。！？!?]{2,24})"
        ),
        re.compile(
            r"my\s+name\s+is\s+([A-Za-z][A-Za-z .'-]{1,40})",
            re.IGNORECASE,
        ),
        re.compile(r"(?:我叫|我的名字是)\s*([\u3400-\u9fff·•]{2,12})"),
    )
    _HONORIFIC_PATTERN = re.compile(
        r"([A-Za-z\u3400-\u9fff々〆ヵヶぁ-んァ-ヶー]{2,16})"
        r"(さん|ちゃー*ん|ちゃん|君|くん|様|さま|先生|先輩|後輩)"
    )
    _TRAILING_KANJI = re.compile(r"([\u3400-\u9fff々〆ヵヶ]{2,8})$")
    _TRAILING_KATAKANA = re.compile(r"([ァ-ヶー]{2,8})$")
    _TRAILING_HIRAGANA = re.compile(r"([ぁ-んー]{2,12})$")
    _TRAILING_LATIN = re.compile(r"([A-Za-z][A-Za-z'-]{1,39})$")
    _TRAILING_INTRODUCTION_WORDS = re.compile(
        r"(?:です|だ|と申します|といいます|と言います|here)$",
        re.IGNORECASE,
    )
    _COMMON_NON_NAMES = {
        "お兄",
        "お姉",
        "おじ",
        "おば",
        "お母",
        "お父",
        "あなた",
        "お前",
        "みな",
        "皆様",
    }
    _CANDIDATE_EDGE_PUNCTUATION = " \t\r\n,，、:：;；。.!！?？…〜～♡♥「」『』（）()[]【】"

    @classmethod
    def discover(cls, project_context: str) -> list[GlossaryCandidate]:
        candidates: dict[str, dict[str, Any]] = {}
        for raw_line in str(project_context or "").splitlines():
            evidence = raw_line.strip()
            if not evidence or evidence == "...":
                continue
            text = cls._line_text(evidence)
            for pattern in cls._SELF_INTRODUCTION_PATTERNS:
                for match in pattern.finditer(text):
                    full_name = cls._clean_candidate(match.group(1))
                    cls._add_candidate(candidates, full_name, "self_introduction", evidence)
                    for name_part in re.split(r"\s+", full_name):
                        if cls._looks_like_name_part(name_part):
                            cls._add_candidate(candidates, name_part, "self_introduction_part", evidence)
            for match in cls._HONORIFIC_PATTERN.finditer(text):
                cls._add_candidate(
                    candidates,
                    cls._clean_honorific_candidate(match.group(1), match.group(2)),
                    "honorific",
                    evidence,
                )

        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                0 if item["reason"] == "self_introduction" else 1,
                -len(item["evidence"]),
                -len(item["source"]),
                item["source"],
            ),
        )
        return [
            GlossaryCandidate(
                source=item["source"],
                reason=item["reason"],
                evidence=tuple(item["evidence"][:3]),
            )
            for item in ordered[: cls.MAX_CANDIDATES]
        ]

    @classmethod
    def missing_candidates(
        cls,
        candidates: Iterable[GlossaryCandidate],
        entries: Iterable[Mapping[str, Any]],
    ) -> list[GlossaryCandidate]:
        covered = [
            cls._candidate_key(str(entry.get("source") or ""))
            for entry in entries
            if isinstance(entry, Mapping)
        ]
        covered = [source for source in covered if source]
        return [
            candidate
            for candidate in candidates
            if not cls._is_covered_by_model_term(candidate.source, covered)
        ]

    @classmethod
    def format_evidence(cls, candidates: Iterable[GlossaryCandidate]) -> str:
        evidence_lines: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            for evidence in candidate.evidence:
                if evidence and evidence not in seen:
                    evidence_lines.append(evidence[: cls.MAX_EVIDENCE_LINE_CHARS])
                    seen.add(evidence)
                if len(evidence_lines) >= cls.MAX_EVIDENCE_LINES:
                    return "\n".join(f"- {line}" for line in evidence_lines)
        return "\n".join(f"- {line}" for line in evidence_lines)

    @classmethod
    def _add_candidate(
        cls,
        candidates: dict[str, dict[str, Any]],
        source: str,
        reason: str,
        evidence: str,
    ) -> None:
        source = cls._clean_candidate(source)
        key = cls._candidate_key(source)
        if not key or source in cls._COMMON_NON_NAMES or not cls._looks_like_candidate(source):
            return
        current = candidates.setdefault(
            key,
            {"source": source, "reason": reason, "evidence": []},
        )
        if reason == "self_introduction":
            current["reason"] = reason
            current["source"] = source
        if evidence not in current["evidence"]:
            current["evidence"].append(evidence)

    @classmethod
    def _clean_candidate(cls, source: str) -> str:
        cleaned = str(source or "").strip(cls._CANDIDATE_EDGE_PUNCTUATION)
        cleaned = cls._TRAILING_INTRODUCTION_WORDS.sub("", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip(cls._CANDIDATE_EDGE_PUNCTUATION)

    @classmethod
    def _clean_honorific_candidate(cls, source: str, honorific: str) -> str:
        cleaned = cls._clean_candidate(source)
        for pattern in (cls._TRAILING_KANJI, cls._TRAILING_KATAKANA, cls._TRAILING_LATIN):
            match = pattern.search(cleaned)
            if match:
                return match.group(1)

        match = cls._TRAILING_HIRAGANA.search(cleaned)
        if not match or honorific in {"さん", "さま"}:
            return ""
        # Hiragana nicknames are common before "chan", but a long run usually
        # includes conversational text because Japanese does not use spaces.
        return match.group(1)[-5:]

    @classmethod
    def _looks_like_candidate(cls, source: str) -> bool:
        compact = cls._candidate_key(source)
        if not 2 <= len(compact) <= 40:
            return False
        return bool(re.search(r"[A-Za-z\u3400-\u9fff々ぁ-んァ-ヶ]", compact))

    @classmethod
    def _looks_like_name_part(cls, source: str) -> bool:
        compact = cls._candidate_key(source)
        return 2 <= len(compact) <= 12 and bool(
            re.fullmatch(r"[A-Za-z\u3400-\u9fff々ぁ-んァ-ヶー·•'-]+", compact)
        )

    @staticmethod
    def _candidate_key(source: str) -> str:
        return re.sub(r"[\s·•・'’-]+", "", str(source or "")).casefold()

    @classmethod
    def _is_covered_by_model_term(cls, candidate: str, model_terms: Iterable[str]) -> bool:
        candidate_key = cls._candidate_key(candidate)
        if not candidate_key:
            return True
        return any(
            candidate_key == model_key
            or (
                len(candidate_key) >= 2
                and len(model_key) >= 2
                and (candidate_key in model_key or model_key in candidate_key)
            )
            for model_key in model_terms
        )

    @staticmethod
    def _line_text(line: str) -> str:
        return re.sub(r"^\d+\.\s*\[[^\]]*\]\s*", "", str(line or "")).strip()
