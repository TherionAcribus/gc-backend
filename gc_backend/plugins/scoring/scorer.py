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

# Precompiled regex patterns for hot paths
_RE_NON_ALPHA = re.compile(r'[^A-Z]')
_RE_WORD_TOKENS = re.compile(r"[\w']+", re.UNICODE)
_RE_GPS_CARDINAL = re.compile(r'\b[NS]\b', re.IGNORECASE)
_RE_GPS_EW = re.compile(r'\b[EW]\b', re.IGNORECASE)
_RE_GPS_WORDS_NS = re.compile(r'\b(nord|north|sud|south)\b', re.IGNORECASE)
_RE_GPS_WORDS_EW = re.compile(r'\b(est|east|ouest|west)\b', re.IGNORECASE)
_RE_GPS_DEGREE = re.compile(r'\d{1,3}\s*[°º]')
_RE_GPS_DMS = re.compile(r'\d+\s*[°º]\s*\d+\s*[\'\u2032\u2019]\s*\d')
_RE_GPS_DECIMAL = re.compile(r'-?\d{1,3}\.\d{3,}[\s,]+-?\d{1,3}\.\d{3,}')
_RE_GPS_COMPACT = re.compile(r'\b[3-5]\d{6}\s+[0-3]\d{5,7}\b')

# Patterns for detecting still-encoded output (hex pairs, numeric codes)
_RE_HEX_PAIRS = re.compile(r'(?:[0-9A-Fa-f]{2}\s){4,}')
_RE_NUMERIC_CODED = re.compile(r'(?:\d{1,3}\s){5,}')
_RE_BASE64_LIKE = re.compile(r'^[A-Za-z0-9+/=]{20,}$')

# Separators that indicate numeric structure (point, virgule, etc.) for number_richness
_NR_SEPARATORS = frozenset({
    'point', 'dot', 'comma', 'virgule', 'komma', 'punto', 'ponto', 'przecinek',
    'et', 'and', 'und',
})


def _normalize_basic(text: str) -> str:
    text = unicodedata.normalize('NFKC', text or '')
    text = text.replace('\u00a0', ' ')
    return text


def _normalize_for_stats(text: str) -> str:
    text = _normalize_basic(text)
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = _RE_NON_ALPHA.sub('', text)
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
    tokens = _RE_WORD_TOKENS.findall(text)
    return [t for t in tokens if len(t) >= 2]


def _gps_gatekeeper_fast(text: str) -> bool:
    if not text:
        return False
    # Cardinal letters N/S + E/W
    if _RE_GPS_CARDINAL.search(text) and _RE_GPS_EW.search(text):
        return True
    # Written cardinal words (FR + EN)
    if _RE_GPS_WORDS_NS.search(text) and _RE_GPS_WORDS_EW.search(text):
        return True
    # Degree symbol (DDM or DMS)
    if _RE_GPS_DEGREE.search(text):
        return True
    # DMS with prime/double-prime markers: 48° 51' 24"
    if _RE_GPS_DMS.search(text):
        return True
    # Decimal degree pair: 48.1234, 2.3456 or 48.1234 2.3456
    if _RE_GPS_DECIMAL.search(text):
        return True
    # Compact numeric pair (geocaching): 7-digit + 6-8 digit
    if _RE_GPS_COMPACT.search(text):
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


def _quadgram_fitness_for_lang(letters: str, lang: str) -> float:
    """Compute quadgram fitness for a specific language."""
    table = load_quadgrams(lang)
    if not table:
        return 0.0
    n = len(letters)
    total = 0.0
    hits = 0
    windows = 0
    for i in range(n - 3):
        q = letters[i:i + 4]
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


def _quadgram_fitness(text: str, lang: str) -> float:
    """Best-of quadgram fitness across detected language + fallback langs."""
    letters = _normalize_for_stats(text)
    if len(letters) < 12:
        return 0.0
    # Try detected language first, then fallback to all available
    langs_to_try = [lang] if lang and lang != 'unknown' else []
    for fallback in ('en', 'fr', 'de'):
        if fallback not in langs_to_try:
            langs_to_try.append(fallback)
    best = 0.0
    for lg in langs_to_try:
        f = _quadgram_fitness_for_lang(letters, lg)
        if f > best:
            best = f
    return best


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


# ── Module-level constants for _coord_words_feature (built once) ──────────

_CW_DIRECTIONS = frozenset({
    'n', 's', 'e', 'w', 'o',
    'nord', 'sud', 'est', 'ouest',
    'north', 'south', 'east', 'west',
    'norte', 'sur', 'este', 'oeste',
    'noord', 'zuid', 'oost',
    'sul', 'leste',
    'polnoc', 'poludnie', 'wschod', 'zachod',
    'sued', 'ost',
})

_CW_LATLON = frozenset({
    'lat', 'lon', 'latitude', 'longitude',
    'breite', 'laenge', 'lange',
    'latitud', 'longitud',
    'latitudine', 'longitudine',
    'breedte', 'lengte',
})

_CW_UNITS = frozenset({
    'deg', 'degre', 'degres', 'degree', 'degrees', 'grad', 'grado', 'grados', 'grau', 'graus',
    'min', 'minute', 'minutes', 'minut', 'minuten', 'minutos', 'minuto', 'minuti',
    'sec', 'seconde', 'secondes', 'sekunde', 'sekunden', 'segundo', 'segundos', 'second', 'seconds',
    'point', 'dot', 'comma', 'virgule', 'komma', 'punto', 'ponto', 'przecinek',
})

_CW_NUMBER_WORDS: frozenset = frozenset({
    # English
    'zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
    'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen',
    'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'hundred', 'thousand',
    # French
    'un', 'une', 'deux', 'trois', 'quatre', 'cinq', 'sept', 'huit', 'neuf',
    'dix', 'onze', 'douze', 'treize', 'quatorze', 'quinze', 'seize', 'dixsept', 'dixhuit', 'dixneuf',
    'vingt', 'trente', 'quarante', 'cinquante', 'soixante', 'cent', 'mille',
    # Spanish / Portuguese
    'cero', 'uno', 'una', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve',
    'diez', 'once', 'doce', 'trece', 'catorce', 'dieciseis', 'diecisiete', 'dieciocho', 'diecinueve',
    'veinte', 'treinta', 'cuarenta', 'cincuenta', 'sesenta', 'cien', 'ciento', 'mil',
    'um', 'uma', 'dois', 'duas', 'sete', 'oito', 'nove',
    'dez', 'dezesseis', 'dezessete', 'dezoito', 'dezenove',
    'vinte', 'trinta', 'cinquenta', 'sessenta', 'cem', 'cento',
    # Italian / Dutch / Polish
    'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto',
    'dieci', 'undici', 'dodici', 'tredici', 'quattordici', 'quindici', 'sedici', 'diciassette', 'diciotto', 'diciannove',
    'venti', 'quaranta', 'cinquanta', 'sessanta', 'mille',
    'nul', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen',
    'tien', 'elf', 'twaalf', 'dertien', 'veertien', 'vijftien', 'zestien', 'zeventien', 'achttien', 'negentien',
    'twintig', 'dertig', 'veertig', 'vijftig', 'zestig', 'honderd', 'duizend',
    'jeden', 'jedna', 'dwa', 'trzy', 'cztery', 'piec', 'szesc', 'siedem', 'osiem', 'dziewiec',
    'dziesiec', 'jedenascie', 'dwanascie', 'trzynascie', 'czternascie', 'pietnascie', 'szesnascie',
    'siedemnascie', 'osiemnascie', 'dziewietnascie', 'dwadziescia', 'trzydziesci', 'czterdziesci', 'piecdziesiat',
    'szescdziesiat', 'sto', 'tysiac',
    # German
    'null', 'eins', 'ein', 'eine', 'zwei', 'drei', 'funf', 'fuenf', 'sechs', 'sieben',
    'zehn', 'zwolf', 'zwoelf', 'dreizehn', 'vierzehn', 'funfzehn', 'fuenfzehn', 'sechzehn', 'siebzehn',
    'achtzehn', 'neunzehn', 'zwanzig', 'dreissig', 'vierzig', 'funfzig', 'fuenfzig', 'sechzig',
    'hundert', 'tausend', 'und',
})

_CW_DE_BASES = ('eins', 'ein', 'zwei', 'drei', 'vier', 'funf', 'fuenf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'zwolf', 'zwoelf')
_CW_DE_TENS = ('zwanzig', 'dreissig', 'vierzig', 'funfzig', 'fuenfzig', 'sechzig')


def _looks_like_number_word(t: str, lang: str) -> bool:
    if not t:
        return False
    if any(ch.isdigit() for ch in t):
        return True
    if t in _CW_NUMBER_WORDS:
        return True
    if lang == 'de':
        for base in _CW_DE_BASES:
            if base in t:
                return True
        for ten in _CW_DE_TENS:
            if ten in t:
                return True
    return False


def _coord_words_feature(text: str, lang: str) -> float:
    raw_tokens = _tokenize_words(text)
    if not raw_tokens:
        return 0.0

    def norm_token(t: str) -> str:
        s = unicodedata.normalize('NFKD', t)
        s = ''.join(ch for ch in s if not unicodedata.combining(ch))
        return s.lower()

    tokens = [norm_token(t) for t in raw_tokens]

    has_dir = any(t in _CW_DIRECTIONS for t in tokens)
    has_latlon = any(t in _CW_LATLON for t in tokens)

    num_hits = sum(1 for t in tokens if _looks_like_number_word(t, lang))

    # ── Relaxation: partial credit without direction if strong number + separator ──
    # Texts like "vingt deux point quatre cent dix sept" have no direction but
    # are very valuable in geocaching (coordinate fragments written as words).
    if not (has_dir or has_latlon):
        if num_hits >= 3:
            has_sep = any(t in _NR_SEPARATORS for t in tokens)
            if has_sep:
                # Strong signal: many number words + a separator like "point"/"virgule"
                num_ratio_relaxed = min(1.0, num_hits / 8.0)
                return float(min(0.7, 0.35 + 0.35 * num_ratio_relaxed))
            elif num_hits >= 5:
                # Weaker signal: lots of number words without separator
                num_ratio_relaxed = min(1.0, num_hits / 10.0)
                return float(min(0.5, 0.25 + 0.25 * num_ratio_relaxed))
        return 0.0

    has_unit = any(t in _CW_UNITS for t in tokens)

    # Sans unités explicites (ex: "N 48 33 787 E 006 38 803"), le GPS strict couvre déjà.
    # Ici on accepte quand même un signal si on a assez de nombres/nombres-en-mots.
    has_numeric_signal = num_hits >= 4
    if not (has_unit or has_numeric_signal):
        return 0.0

    num_ratio = min(1.0, num_hits / 10.0)
    base = 0.6 if has_unit else 0.45
    return float(min(1.0, base + 0.4 * num_ratio))


# ── Encoded-pattern penalty ──────────────────────────────────────────────
def _encoded_pattern_penalty(text: str) -> float:
    """Detect still-encoded output (hex pairs, numeric codes, base64).

    Returns a penalty factor in [0..1]:
      - 1.0 = text looks clean (no encoded patterns)
      - 0.0 = text looks entirely encoded (hex/numeric/base64)

    This catches outputs where a cipher plugin barely transformed the input,
    leaving hex pairs like ``76 69 6E 67 74`` or numeric codes like ``12 34 56``.
    """
    if not text or len(text) < 8:
        return 1.0

    stripped = text.strip()
    total_len = len(stripped)

    # Base64-like: single long block of base64 chars
    if _RE_BASE64_LIKE.match(stripped):
        return 0.1

    # Hex pairs: "6E 67 74 20 64 65"
    hex_matches = _RE_HEX_PAIRS.findall(stripped)
    hex_coverage = sum(len(m) for m in hex_matches) / max(1, total_len) if hex_matches else 0.0
    if hex_coverage > 0.6:
        return 0.05
    if hex_coverage > 0.3:
        return 0.2

    # Numeric coded: "76 69 6 67 74" (sequences of short numbers separated by spaces)
    num_matches = _RE_NUMERIC_CODED.findall(stripped)
    num_coverage = sum(len(m) for m in num_matches) / max(1, total_len) if num_matches else 0.0
    if num_coverage > 0.6:
        return 0.15
    if num_coverage > 0.3:
        return 0.35

    return 1.0


# ── Number richness feature ─────────────────────────────────────────────
def _number_richness_feature(text: str, lang: str) -> float:
    """Detect text rich in number words or digits, even without coordinate markers.

    In geocaching, decoded text containing number words (e.g. "vingt deux point
    quatre cent dix sept") or digit sequences is extremely valuable — it likely
    represents coordinate components, puzzle answers, or other numeric data.

    Returns a float in [0..1]:
      - 0.0 = no significant number content
      - 1.0 = text is predominantly numbers/number-words

    This feature is intentionally INDEPENDENT of direction signals (N/S/E/W)
    and coordinate units.  It complements ``_coord_words_feature`` which
    requires direction context.
    """
    raw_tokens = _tokenize_words(text)
    if not raw_tokens or len(raw_tokens) < 2:
        return 0.0

    def norm_token(t: str) -> str:
        s = unicodedata.normalize('NFKD', t)
        s = ''.join(ch for ch in s if not unicodedata.combining(ch))
        return s.lower()

    tokens = [norm_token(t) for t in raw_tokens]

    num_count = 0
    sep_count = 0
    for t in tokens:
        if t in _CW_NUMBER_WORDS:
            # Actual number word from dictionary — always counts
            num_count += 1
        elif t.isdigit():
            # Pure digit token: only count if it's a reasonable number (1-5 digits)
            # and not binary-like (only 0s and 1s with length > 4)
            if len(t) <= 5 and not (len(t) > 4 and set(t) <= {'0', '1'}):
                num_count += 1
        elif t in _NR_SEPARATORS:
            sep_count += 1
        # Skip mixed alphanumeric tokens (e.g. "6E", "XJ12") — not meaningful numbers

    if num_count < 2:
        return 0.0

    # Ratio of number-related tokens (number words + separators) vs total
    relevant = num_count + sep_count
    ratio = relevant / len(tokens)

    # A separator among numbers is a strong signal (e.g. "vingt deux point quatre")
    sep_bonus = min(0.15, sep_count * 0.08) if sep_count > 0 else 0.0

    # Scale: 2 number hits → ~0.15, 4 → ~0.35, 6+ → ~0.55+, with ratio boost
    density = min(1.0, num_count / 8.0)
    richness = density * 0.55 + ratio * 0.35 + sep_bonus

    return float(min(1.0, richness))


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
    encoded_penalty = _encoded_pattern_penalty(text)
    number_richness = _number_richness_feature(text, langid.language)

    ngram_fitness = float(min(1.0, trigram_fitness * 0.5 + quadgram_fitness * 0.7))
    ngram_fitness *= repetition_quality

    if ic < 0.038 and gps_conf < 0.7 and coord_words < 0.3 and number_richness < 0.2:
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
                        'encoded_penalty': encoded_penalty,
                        'number_richness': number_richness,
                        'lexical_coverage': lexical,
                        'coherence': coherence,
                    },
                    'explanation': 'IC trop faible et aucune coordonnée GPS forte détectée.'
                }
            }
        )

    weights = {
        'gps_confidence': 0.80,
        'ngram_fitness': 0.40,
        'lexical_coverage': 0.30,
        'coord_words': 0.35,
        'number_richness': 0.45,
        'coherence': 0.20,
        'ic_quality': 0.15,
        'repetition_quality': 0.10,
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
            + number_richness * weights['number_richness']
            + coherence * weights['coherence']
            + ic_v * weights['ic_quality']
            + entropy_v * weights['entropy_quality']
        )
        early_exit = None

        # Apply encoded-pattern penalty as a multiplicative factor
        # This heavily penalizes outputs that still look like hex/base64/numeric codes
        if encoded_penalty < 1.0:
            score *= encoded_penalty
            if encoded_penalty < 0.2:
                early_exit = 'encoded_pattern'

        if gps_conf <= 0.0 and ngram_fitness < 0.1 and coord_words < 0.2 and number_richness < 0.15:
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
    if number_richness > 0:
        explanation.append(f"num_rich={number_richness:.2f}")
    if encoded_penalty < 1.0:
        explanation.append(f"enc_pen={encoded_penalty:.2f}")

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
                    'encoded_penalty': float(encoded_penalty),
                    'number_richness': float(number_richness),
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


# ── Fast scoring for bruteforce pre-filtering ────────────────────────

def _compute_ic_from_letters(letters: str) -> float:
    """IC from pre-normalised uppercase-only string."""
    n = len(letters)
    if n < 2:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in letters:
        counts[ch] = counts.get(ch, 0) + 1
    numerator = sum(v * (v - 1) for v in counts.values())
    denominator = n * (n - 1)
    return float(numerator / denominator) if denominator else 0.0


def _repetition_quality_from_letters(letters: str) -> float:
    """Repetition quality from pre-normalised uppercase-only string."""
    if len(letters) < 12:
        return 1.0
    max_run = 1
    run = 1
    for i in range(1, len(letters)):
        if letters[i] == letters[i - 1]:
            run += 1
            if run > max_run:
                max_run = run
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


@lru_cache(maxsize=4096)
def _score_text_fast_cached(letters: str) -> float:
    """Inner cached implementation of score_text_fast."""
    n = len(letters)
    if n < 4:
        return 0.0

    # Tier 0: instant reject on repetition
    rep = _repetition_quality_from_letters(letters)
    if rep <= 0.0:
        return 0.0

    # Tier 0: IC check — reject if clearly random
    ic = _compute_ic_from_letters(letters)
    if ic < 0.035 and n >= 20:
        return 0.0

    # Tier 1: quadgram fitness (try EN, then FR, then DE)
    best_fitness = 0.0
    for lang in ('en', 'fr', 'de'):
        table = load_quadgrams(lang)
        if not table:
            continue
        total = 0.0
        hits = 0
        windows = 0
        for i in range(n - 3):
            q = letters[i:i + 4]
            windows += 1
            v = table.get(q)
            if v is not None:
                hits += 1
                total += float(v)
            else:
                total += -6.0
        if windows > 0:
            mean_logp = total / windows
            hit_ratio = hits / windows
            fitness = (mean_logp + 6.0) / 4.0
            fitness = max(0.0, min(1.0, fitness))
            fitness = min(1.0, fitness * 0.7 + hit_ratio * 0.3)
            if fitness > best_fitness:
                best_fitness = fitness

    # Hard gate: if quadgram fitness is very low, this isn't natural language
    # (monoalphabetic substitutions preserve IC but destroy quadgrams)
    if best_fitness < 0.15:
        return float(best_fitness * 0.3)

    # Combine: quadgram-dominant formula
    ic_norm = max(0.0, min(1.0, (ic - 0.045) / 0.03))
    score = best_fitness * 0.75 + ic_norm * 0.10 + rep * 0.15
    return float(min(1.0, score))


def score_text_fast(text: str) -> float:
    """Cheap tier-0/tier-1 scoring for bruteforce pre-filtering.

    Returns a float 0..1.  Much faster than score_text() because it skips
    GPS detection, language detection, lexical features, and coord_words.
    Uses only: IC, repetition quality, quadgram fitness (EN fallback),
    encoded-pattern penalty, and a lightweight number-word boost.

    Typical cost: ~0.05 ms per call vs ~1-5 ms for full score_text().
    Results are LRU-cached (4096 entries) keyed by normalised letters.
    """
    # Early penalty for still-encoded output (hex pairs, base64, etc.)
    enc_pen = _encoded_pattern_penalty(text)
    if enc_pen < 0.1:
        return 0.0

    letters = _normalize_for_stats(text)
    if len(letters) < 4:
        # Very few letters — check if the text is rich in number words
        # (e.g. "vingt deux point quatre cent dix sept" has many letters
        #  but some decoded outputs are short). If letters < 4, the
        #  quadgram path can't help, but number richness might.
        nr = _number_richness_feature(text, 'fr')
        if nr > 0.3:
            return float(min(0.5, nr * enc_pen))
        return 0.0

    base_score = _score_text_fast_cached(letters)

    # Apply encoded-pattern penalty
    if enc_pen < 1.0:
        base_score *= enc_pen

    # Lightweight number-word boost: if quadgrams gave a decent score AND
    # the text is rich in number words, nudge the score up so the full
    # scorer gets a chance to evaluate it properly.
    if base_score > 0.05:
        nr = _number_richness_feature(text, 'fr')
        if nr > 0.2:
            base_score = min(1.0, base_score + nr * 0.15)

    return float(base_score)


def score_and_rank_results(
    results: List[Dict[str, Any]],
    *,
    top_k: int = 25,
    min_score: float = 0.05,
    fast_reject_threshold: float = 0.02,
    context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Score a batch of plugin results with tiered pruning and top-K selection.

    Pipeline:
      1. Tier 0 — fast reject via score_text_fast() (< 0.1ms each)
      2. Sort survivors by fast score, keep top max(top_k*3, 75)
      3. Tier 1 — full score_text() on survivors
      4. Filter by min_score, sort descending, return top_k

    Each result dict must have 'text_output'.  The function mutates results
    in place (adds/updates 'confidence' and 'metadata.scoring').

    Returns the ranked, pruned list.
    """
    if not results:
        return []

    # Collect valid items
    valid_items: List[Dict[str, Any]] = []
    for item in results:
        text_output = item.get('text_output')
        if isinstance(text_output, str) and text_output.strip():
            valid_items.append(item)

    if not valid_items:
        return []

    # For small result sets, skip fast-reject and score everything directly
    use_fast_filter = len(valid_items) > top_k

    if use_fast_filter:
        # Phase 1: fast scoring for bruteforce-sized result sets
        scored_fast: List[Tuple[float, int, Dict[str, Any]]] = []
        for idx, item in enumerate(valid_items):
            text_output = item['text_output']
            fast_score = score_text_fast(text_output)
            if fast_score < fast_reject_threshold:
                continue
            scored_fast.append((fast_score, idx, item))

        # Phase 2: keep the best candidates for full scoring
        scored_fast.sort(key=lambda x: x[0], reverse=True)
        tier2_limit = max(top_k * 3, 75)
        candidates = [item for (_fs, _i, item) in scored_fast[:tier2_limit]]
    else:
        candidates = valid_items

    # Phase 3: full scoring on survivors
    ranked: List[Dict[str, Any]] = []
    for item in candidates:
        text_output = item['text_output']
        scored = score_text(text_output, context=context)
        full_score = float(scored.get('score') or 0.0)

        item['confidence'] = full_score
        item_metadata = item.get('metadata')
        if not isinstance(item_metadata, dict):
            item_metadata = {}
            item['metadata'] = item_metadata
        scoring_meta = scored.get('metadata')
        if isinstance(scoring_meta, dict) and isinstance(scoring_meta.get('scoring'), dict):
            item_metadata['scoring'] = scoring_meta['scoring']

        ranked.append(item)

    # Phase 4: filter by min_score, sort, and return top-K
    if min_score > 0:
        ranked = [r for r in ranked if r.get('confidence', 0.0) >= min_score]
    ranked.sort(key=lambda r: r.get('confidence', 0.0), reverse=True)
    return ranked[:top_k]
