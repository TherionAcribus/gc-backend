from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .resources_loader import load_lang_trigrams


DEFAULT_LANGS_EUROPE: List[str] = [
    'fr', 'en', 'de', 'es', 'it', 'nl', 'pt', 'pl'
]


@dataclass(frozen=True)
class LangIdResult:
    language: str
    confidence: float
    candidates: List[Tuple[str, float]]


def _normalize_for_trigrams(text: str) -> str:
    text = unicodedata.normalize('NFKC', text or '')
    text = text.lower()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_trigrams(text: str) -> List[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < 3:
        return []
    trigrams: List[str] = []
    for i in range(len(compact) - 2):
        tri = compact[i : i + 3]
        if ' ' in tri:
            continue
        trigrams.append(tri)
    return trigrams


def detect_language(text: str, langs: Optional[Sequence[str]] = None) -> LangIdResult:
    langs = list(langs) if langs else list(DEFAULT_LANGS_EUROPE)

    normalized = _normalize_for_trigrams(text)
    trigrams = _extract_trigrams(normalized)
    if len(trigrams) < 8:
        return LangIdResult(language='unknown', confidence=0.0, candidates=[])

    total = len(trigrams)
    trigram_set = set(trigrams)

    scores: List[Tuple[str, float]] = []
    for lang in langs:
        profile = load_lang_trigrams(lang)
        if not profile:
            continue
        hits = sum(1 for t in trigram_set if t in profile)
        score = hits / max(1, min(total, 100))
        scores.append((lang, float(score)))

    scores.sort(key=lambda x: x[1], reverse=True)
    if not scores:
        return LangIdResult(language='unknown', confidence=0.0, candidates=[])

    best_lang, best = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0

    confidence = best
    if best < 0.08:
        return LangIdResult(language='unknown', confidence=float(best), candidates=scores[:3])

    if best - second < 0.02:
        confidence = max(0.0, best - second)

    return LangIdResult(language=best_lang, confidence=float(min(1.0, confidence)), candidates=scores[:3])
