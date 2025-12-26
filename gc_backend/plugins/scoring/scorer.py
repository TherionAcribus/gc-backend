from __future__ import annotations

import copy
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .langid import DEFAULT_LANGS_EUROPE, detect_language
from .resources_loader import load_geo_terms, load_quadgrams, load_stopwords


_STOPLIST_GEO = {
    'n', 's', 'e', 'w', 'o',
    'nord', 'sud', 'est', 'ouest',
    'north', 'south', 'east', 'west',
}


def _normalize_basic(text: str) -> str:
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('\u00a0', ' ')
    return text


def _normalize_for_stats(text: str) -> str:
    text = _normalize_basic(text)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = re.sub(r'[^A-Z]', '', text)
    return text


def _compute_ic(text: str) -> float:
    letters = _normalize_for_stats(text)
    n = len(letters)
    if n < 2:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in letters:
        counts[ch] = counts.get(ch, 0) + 1
    numerator = sum(v * (v - 1) for v in counts.values())
    denominator = n * (n - 1)
    return float(numerator / denominator) if denominator else 0.0


def _shannon_entropy(text: str) -> float:
    s = _normalize_basic(text)
    if not s:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    entropy = 0.0
    for c in counts.values():
        p = c / n
        entropy -= p * math.log2(p)
    return float(entropy)


def _tokenize_words(text: str) -> List[str]:
    text = unicodedata.normalize('NFKC', text or '')
    text = text.lower()
    tokens = re.findall(r"[\w']+", text, flags=re.UNICODE)
    return [t for t in tokens if len(t) >= 2]


def _gps_gatekeeper_fast(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\b[NS]\b", text, re.IGNORECASE) and re.search(r"\b[EW]\b", text, re.IGNORECASE):
        return True
    if re.search(r"\b(nord|north)\b", text, re.IGNORECASE) and re.search(r"\b(est|east|ouest|west)\b", text, re.IGNORECASE):
        return True
    if re.search(r"\d{1,3}\s*[°º]", text):
        return True
    return False


def _detect_gps_confidence(text: str, include_numeric_only: bool = False) -> Tuple[float, Dict[str, Any]]:
    if not _gps_gatekeeper_fast(text):
        return 0.0, {"exist": False}

    try:
        from gc_backend.blueprints.coordinates import detect_gps_coordinates

        coords = detect_gps_coordinates(text, include_numeric_only=include_numeric_only)
        if coords.get('exist'):
            return float(coords.get('confidence') or 0.0), coords
        return 0.0, coords
    except Exception as e:
        logger.debug(f"GPS detect error: {e}")
        return 0.0, {"exist": False, "error": str(e)}


def _entropy_feature(entropy: float) -> float:
    if entropy <= 0.0:
        return 0.0
    if entropy < 1.5:
        return 0.0
    if 1.5 <= entropy <= 4.6:
        return 1.0
    if entropy <= 5.2:
        return 0.4
    return 0.1


def _ic_feature(ic: float) -> float:
    return max(0.0, min(1.0, (ic - 0.045) / 0.03))


def _lexical_features(tokens: List[str], lang: str) -> Tuple[float, float, List[str]]:
    if not tokens:
        return 0.0, 0.0, []

    stopwords = load_stopwords(lang) if lang != 'unknown' else frozenset()
    geo_terms = load_geo_terms(lang) if lang != 'unknown' else frozenset()

    filtered: List[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in _STOPLIST_GEO:
            continue
        if stopwords and tl in stopwords:
            continue
        filtered.append(tl)

    if not filtered:
        return 0.0, 0.0, []

    recognized: List[str] = []
    for t in filtered:
        if t in geo_terms:
            recognized.append(t)

    coverage = min(1.0, len(filtered) / 12.0)
    geo_bonus = min(1.0, len(recognized) / max(1, len(filtered)))
    lexical = min(1.0, coverage * 0.8 + geo_bonus * 0.6)

    longest_run = 0
    current = 0
    for t in filtered:
        if len(t) >= 3:
            current += 1
            longest_run = max(longest_run, current)
        else:
            current = 0
    coherence = min(1.0, longest_run / 5.0)

    words_found = recognized[:50]

    return float(lexical), float(coherence), words_found


def _quadgram_fitness(text: str, lang: str) -> float:
    letters = _normalize_for_stats(text)
    if len(letters) < 12:
        return 0.0
    table = load_quadgrams(lang)
    if not table and lang != 'en':
        table = load_quadgrams('en')
    if not table:
        return 0.0

    total = 0.0
    hits = 0
    windows = 0
    for i in range(len(letters) - 3):
        q = letters[i : i + 4]
        windows += 1
        v = table.get(q)
        if v is not None:
            hits += 1
            total += float(v)
        else:
            total += -6.0

    if windows <= 0:
        return 0.0

    mean_logp = total / windows
    hit_ratio = hits / windows
    fitness = (mean_logp + 6.0) / 4.0
    fitness = max(0.0, min(1.0, fitness))
    return float(min(1.0, fitness * 0.7 + hit_ratio * 0.3))


def _repetition_quality(text: str) -> float:
    letters = _normalize_for_stats(text)
    if len(letters) < 12:
        return 1.0

    max_run = 1
    run = 1
    for i in range(1, len(letters)):
        if letters[i] == letters[i - 1]:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1

    if max_run >= 5:
        return 0.0
    if max_run == 4:
        return 0.2
    if max_run == 3:
        return 0.6

    unique_ratio = len(set(letters)) / max(1, len(letters))
    if unique_ratio < 0.12:
        return 0.2
    if unique_ratio < 0.18:
        return 0.6
    return 1.0


def _coord_words_feature(text: str, lang: str) -> float:
    raw_tokens = _tokenize_words(text)
    if not raw_tokens:
        return 0.0

    def norm_token(t: str) -> str:
        s = unicodedata.normalize('NFKD', t)
        s = ''.join(ch for ch in s if not unicodedata.combining(ch))
        return s.lower()

    tokens = [norm_token(t) for t in raw_tokens]

    directions = {
        'n', 's', 'e', 'w', 'o',
        'nord', 'sud', 'est', 'ouest',
        'north', 'south', 'east', 'west',
        'norte', 'sur', 'este', 'oeste',
        'noord', 'zuid', 'oost',
        'norte', 'sul', 'leste', 'oeste',
        'polnoc', 'poludnie', 'wschod', 'zachod',
        'sued', 'sud', 'ost', 'west', 'nord'
    }
    latlon_words = {
        'lat', 'lon', 'latitude', 'longitude',
        'breite', 'laenge', 'lange',
        'latitud', 'longitud',
        'latitudine', 'longitudine',
        'breedte', 'lengte',
    }
    units = {
        'deg', 'degre', 'degres', 'degres', 'degree', 'degrees', 'grad', 'grado', 'grados', 'grau', 'graus',
        'min', 'minute', 'minutes', 'minut', 'minuten', 'minutos', 'minuto', 'minuti',
        'sec', 'seconde', 'secondes', 'sekunde', 'sekunden', 'segundo', 'segundos', 'second', 'seconds',
        'point', 'dot', 'comma', 'virgule', 'komma', 'punto', 'ponto', 'przecinek'
    }

    number_words_common = {
        'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
        'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen',
        'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'hundred', 'thousand'
    }
    number_words_fr = {
        'zero', 'un', 'une', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf',
        'dix', 'onze', 'douze', 'treize', 'quatorze', 'quinze', 'seize', 'dixsept', 'dixhuit', 'dixneuf',
        'vingt', 'trente', 'quarante', 'cinquante', 'soixante', 'cent', 'mille'
    }
    number_words_es_pt = {
        'cero', 'uno', 'una', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve',
        'diez', 'once', 'doce', 'trece', 'catorce', 'quince', 'dieciseis', 'diecisiete', 'dieciocho', 'diecinueve',
        'veinte', 'treinta', 'cuarenta', 'cincuenta', 'sesenta', 'cien', 'ciento', 'mil',
        'zero', 'um', 'uma', 'dois', 'duas', 'tres', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove',
        'dez', 'onze', 'doze', 'treze', 'catorze', 'quatorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove',
        'vinte', 'trinta', 'quarenta', 'cinquenta', 'sessenta', 'cem', 'cento', 'mil'
    }
    number_words_it_nl_pl = {
        'zero', 'uno', 'una', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto', 'nove',
        'dieci', 'undici', 'dodici', 'tredici', 'quattordici', 'quindici', 'sedici', 'diciassette', 'diciotto', 'diciannove',
        'venti', 'trenta', 'quaranta', 'cinquanta', 'sessanta', 'cento', 'mille',
        'nul', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen',
        'tien', 'elf', 'twaalf', 'dertien', 'veertien', 'vijftien', 'zestien', 'zeventien', 'achttien', 'negentien',
        'twintig', 'dertig', 'veertig', 'vijftig', 'zestig', 'honderd', 'duizend',
        'zero', 'jeden', 'jedna', 'dwa', 'trzy', 'cztery', 'piec', 'szesc', 'siedem', 'osiem', 'dziewiec',
        'dziesiec', 'jedenascie', 'dwanascie', 'trzynascie', 'czternascie', 'pietnascie', 'szesnascie',
        'siedemnascie', 'osiemnascie', 'dziewietnascie', 'dwadziescia', 'trzydziesci', 'czterdziesci', 'piecdziesiat',
        'szescdziesiat', 'sto', 'tysiac'
    }
    number_words_de = {
        'null', 'eins', 'ein', 'eine', 'zwei', 'drei', 'vier', 'funf', 'fuenf', 'sechs', 'sieben', 'acht', 'neun',
        'zehn', 'elf', 'zwolf', 'zwoelf', 'dreizehn', 'vierzehn', 'funfzehn', 'fuenfzehn', 'sechzehn', 'siebzehn',
        'achtzehn', 'neunzehn', 'zwanzig', 'dreissig', 'dreißig', 'vierzig', 'funfzig', 'fuenfzig', 'sechzig',
        'hundert', 'tausend', 'und'
    }

    number_words_all = set()
    number_words_all |= number_words_common
    number_words_all |= number_words_fr
    number_words_all |= number_words_es_pt
    number_words_all |= number_words_it_nl_pl
    number_words_all |= number_words_de

    def looks_like_number_word(t: str) -> bool:
        if not t:
            return False
        if any(ch.isdigit() for ch in t):
            return True
        if t in number_words_all:
            return True
        # allemand: mots composés (zweiunddreissig, etc.)
        if lang == 'de':
            for base in ('eins', 'ein', 'zwei', 'drei', 'vier', 'funf', 'fuenf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'zwolf', 'zwoelf'):
                if base in t:
                    return True
            for ten in ('zwanzig', 'dreissig', 'dreißig', 'vierzig', 'funfzig', 'fuenfzig', 'sechzig'):
                if ten in t:
                    return True
        return False

    has_dir = any(t in directions for t in tokens)
    has_latlon = any(t in latlon_words for t in tokens)
    if not (has_dir or has_latlon):
        return 0.0

    has_unit = any(t in units for t in tokens)

    num_hits = 0
    for t in tokens:
        if looks_like_number_word(t):
            num_hits += 1

    # Sans unités explicites (ex: "N 48 33 787 E 006 38 803"), le GPS strict couvre déjà.
    # Ici on accepte quand même un signal si on a assez de nombres/nombres-en-mots.
    has_numeric_signal = num_hits >= 4
    if not (has_unit or has_numeric_signal):
        return 0.0

    num_ratio = min(1.0, num_hits / 10.0)
    base = 0.6 if has_unit else 0.45
    return float(min(1.0, base + 0.4 * num_ratio))


@dataclass(frozen=True)
class ScoreResult:
    score: float
    metadata: Dict[str, Any]


def _compute_score(text: str, context: Optional[Dict[str, Any]] = None) -> ScoreResult:
    context = context or {}

    ic = _compute_ic(text)
    entropy = _shannon_entropy(text)

    gps_conf, gps_details = _detect_gps_confidence(text, include_numeric_only=False)

    langid = detect_language(text, DEFAULT_LANGS_EUROPE)
    tokens = _tokenize_words(text)

    lexical, coherence, words_found = _lexical_features(tokens, langid.language)
    ic_v = _ic_feature(ic)
    entropy_v = _entropy_feature(entropy)

    trigram_fitness = float(langid.confidence)
    quadgram_fitness = _quadgram_fitness(text, langid.language)
    repetition_quality = _repetition_quality(text)
    coord_words = _coord_words_feature(text, langid.language)

    ngram_fitness = float(min(1.0, trigram_fitness * 0.5 + quadgram_fitness * 0.7))
    ngram_fitness *= repetition_quality

    if ic < 0.045 and gps_conf < 0.7:
        return ScoreResult(
            score=0.0,
            metadata={
                'scoring': {
                    'score': 0.0,
                    'early_exit': 'ic_veto',
                    'language_detected': langid.language,
                    'language_confidence': langid.confidence,
                    'features': {
                        'ic': ic,
                        'entropy': entropy,
                        'gps_confidence': gps_conf,
                        'ngram_fitness': ngram_fitness,
                        'trigram_fitness': trigram_fitness,
                        'quadgram_fitness': quadgram_fitness,
                        'repetition_quality': repetition_quality,
                        'coord_words': coord_words,
                        'lexical_coverage': lexical,
                        'coherence': coherence,
                    },
                    'explanation': 'IC trop faible et aucune coordonnée GPS forte détectée.'
                }
            }
        )

    weights = {
        'gps_confidence': 0.85,
        'lexical_coverage': 0.40,
        'ngram_fitness': 0.30,
        'repetition_quality': 0.10,
        'coord_words': 0.35,
        'coherence': 0.20,
        'ic_quality': 0.15,
        'entropy_quality': 0.10,
    }

    if gps_conf > 0.9 and ic > 0.05:
        score = 0.98
        early_exit = 'gps_strong'
    else:
        score = (
            gps_conf * weights['gps_confidence']
            + lexical * weights['lexical_coverage']
            + ngram_fitness * weights['ngram_fitness']
            + repetition_quality * weights['repetition_quality']
            + coord_words * weights['coord_words']
            + coherence * weights['coherence']
            + ic_v * weights['ic_quality']
            + entropy_v * weights['entropy_quality']
        )
        early_exit = None

        if gps_conf <= 0.0 and ngram_fitness < 0.1 and coord_words < 0.2:
            score = 0.05
            early_exit = 'ngram_low'

        if gps_conf > 0.7 and lexical > 0.3:
            score += 0.2

        score = min(1.0, float(score))

    explanation = []
    if gps_conf > 0:
        explanation.append(f"GPS={gps_conf:.2f}")
    if langid.language != 'unknown':
        explanation.append(f"lang={langid.language} ({langid.confidence:.2f})")
    explanation.append(f"lex={lexical:.2f}")
    explanation.append(f"coh={coherence:.2f}")

    return ScoreResult(
        score=float(score),
        metadata={
            'scoring': {
                'score': float(score),
                'early_exit': early_exit,
                'language_detected': langid.language,
                'language_confidence': float(langid.confidence),
                'words_found': words_found,
                'gps_patterns': [gps_details.get('ddm')] if gps_details.get('exist') else [],
                'gps_source': gps_details.get('source'),
                'features': {
                    'ic': float(ic),
                    'entropy': float(entropy),
                    'gps_confidence': float(gps_conf),
                    'ngram_fitness': float(ngram_fitness),
                    'trigram_fitness': float(trigram_fitness),
                    'quadgram_fitness': float(quadgram_fitness),
                    'repetition_quality': float(repetition_quality),
                    'coord_words': float(coord_words),
                    'lexical_coverage': float(lexical),
                    'coherence': float(coherence),
                    'ic_quality': float(ic_v),
                    'entropy_quality': float(entropy_v),
                },
                'weights': weights,
                'explanation': ' | '.join(explanation),
            }
        }
    )


def _cache_key(text: str, context: Optional[Dict[str, Any]]) -> str:
    normalized = _normalize_basic(text)
    ctx = ''
    if context and isinstance(context, dict):
        ctx = str(sorted(context.items()))
    raw = (normalized + '|' + ctx).encode('utf-8')
    return hashlib.md5(raw).hexdigest()


@lru_cache(maxsize=1000)
def _cached_score(cache_key: str, text: str, context_json: str) -> ScoreResult:
    _ = cache_key
    context: Optional[Dict[str, Any]] = None
    if context_json:
        try:
            import json
            context = json.loads(context_json)
        except Exception:
            context = None
    return _compute_score(text, context)


def score_text(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    context = context or {}
    try:
        import json
        context_json = json.dumps(context, sort_keys=True)
    except Exception:
        context_json = ''

    key = _cache_key(text, context)
    result = _cached_score(key, text, context_json)
    return {
        'score': result.score,
        'metadata': copy.deepcopy(result.metadata),
    }
