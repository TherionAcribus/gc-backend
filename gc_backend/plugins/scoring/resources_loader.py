from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, List, Set


_RESOURCES_DIR = Path(__file__).resolve().parent / 'resources'


@lru_cache(maxsize=256)
def load_stopwords(lang: str) -> FrozenSet[str]:
    path = _RESOURCES_DIR / 'stopwords' / f'stopwords.{lang}.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    return frozenset(str(x).strip().lower() for x in data if str(x).strip())


@lru_cache(maxsize=256)
def load_geo_terms(lang: str) -> FrozenSet[str]:
    path = _RESOURCES_DIR / 'geo_terms' / f'geo_terms.{lang}.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    return frozenset(str(x).strip().lower() for x in data if str(x).strip())


@lru_cache(maxsize=256)
def load_lang_trigrams(lang: str) -> FrozenSet[str]:
    path = _RESOURCES_DIR / 'langid_trigrams' / f'{lang}.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    trigrams: Set[str] = set()
    for x in data:
        s = str(x).strip().lower()
        if not s:
            continue
        if len(s) != 3:
            s = s[:3]
        if len(s) == 3:
            trigrams.add(s)
    return frozenset(trigrams)


@lru_cache(maxsize=64)
def load_quadgrams(lang: str) -> Dict[str, float]:
    path = _RESOURCES_DIR / 'quadgrams' / f'{lang}.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        return {}
    quadgrams: Dict[str, float] = {}
    for k, v in data.items():
        key = str(k).strip().upper()
        if len(key) != 4:
            continue
        try:
            quadgrams[key] = float(v)
        except Exception:
            continue
    return quadgrams


def available_langs() -> List[str]:
    path = _RESOURCES_DIR / 'langid_trigrams'
    if not path.exists():
        return []
    return sorted([p.stem for p in path.glob('*.json')])
