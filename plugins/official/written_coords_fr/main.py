import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


_UNITS_0_19 = {
    "zero": 0,
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    "onze": 11,
    "douze": 12,
    "treize": 13,
    "quatorze": 14,
    "quinze": 15,
    "seize": 16,
    "dixsept": 17,
    "dixhuit": 18,
    "dixneuf": 19,
}

_TENS = {
    "vingt": 20,
    "trente": 30,
    "quarante": 40,
    "cinquante": 50,
    "soixante": 60,
}

_DIGIT_WORDS = {
    "zero": "0",
    "un": "1",
    "une": "1",
    "deux": "2",
    "trois": "3",
    "quatre": "4",
    "cinq": "5",
    "six": "6",
    "sept": "7",
    "huit": "8",
    "neuf": "9",
}

_DIRECTION_LAT = {
    "n": "N",
    "nord": "N",
    "s": "S",
    "sud": "S",
}

_DIRECTION_LON = {
    "e": "E",
    "est": "E",
    "o": "W",
    "ouest": "W",
    "w": "W",
}

_DEG_WORDS = {"deg", "degre", "degres", "degree", "degrees"}
_MIN_WORDS = {"min", "minute", "minutes"}
_DEC_SEP_WORDS = {"virgule", "comma", "point", "dot"}
_CONNECTORS = {"et", "d", "de", "du", "des", "la", "le", "l", "a"}


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()


def _tokenize(text: str) -> List[str]:
    s = _fold(text)
    s = s.replace("'", " ").replace("’", " ")
    s = s.replace("-", " ")
    return re.findall(r"[a-z]+|\d+|[.,]", s)


def _build_vocab() -> List[str]:
    vocab = set()
    vocab |= set(_UNITS_0_19.keys())
    vocab |= set(_TENS.keys())
    vocab |= set(_DIGIT_WORDS.keys())
    vocab |= set(_DIRECTION_LAT.keys())
    vocab |= set(_DIRECTION_LON.keys())
    vocab |= _DEG_WORDS
    vocab |= _MIN_WORDS
    vocab |= _DEC_SEP_WORDS
    vocab |= _CONNECTORS
    vocab |= {"cent", "cents", "mille"}
    vocab |= {"quatrevingt", "quatrevingts", "soixantedix", "quatrevingtdix"}
    vocab |= {"latitude", "longitude"}
    return sorted(vocab, key=len, reverse=True)


_VOCAB = _build_vocab()


def _deconcat_alpha_token(token: str) -> List[str]:
    if not token.isalpha() or len(token) < 14:
        return [token]

    out: List[str] = []
    i = 0
    while i < len(token):
        matched = None
        for w in _VOCAB:
            if token.startswith(w, i):
                matched = w
                break
        if matched:
            out.append(matched)
            i += len(matched)
        else:
            i += 1

    return out or [token]


def _tokenize_with_deconcat(text: str, include_deconcat: bool) -> List[str]:
    toks = _tokenize(text)
    if not include_deconcat:
        return toks

    out: List[str] = []
    for t in toks:
        if t.isalpha() and len(t) >= 14:
            out.extend(_deconcat_alpha_token(t))
        else:
            out.append(t)
    return out


def _skip_connectors(tokens: List[str], i: int) -> int:
    while i < len(tokens) and tokens[i] in _CONNECTORS:
        i += 1
    return i


def _parse_fr_int(tokens: List[str], i: int) -> Tuple[Optional[int], int]:
    if i >= len(tokens):
        return None, i

    if tokens[i].isdigit():
        try:
            return int(tokens[i]), i + 1
        except Exception:
            return None, i

    j = i
    digits: List[str] = []
    while j < len(tokens) and len(digits) < 6:
        t2 = tokens[j]
        if t2 in _DIGIT_WORDS:
            digits.append(_DIGIT_WORDS[t2])
            j += 1
            continue
        if t2.isdigit() and len(t2) == 1:
            digits.append(t2)
            j += 1
            continue
        break

    if len(digits) >= 2:
        try:
            return int("".join(digits)), j
        except Exception:
            return None, i

    t = tokens[i]

    if t in {"cent", "cents"}:
        j = i + 1
        rest, j2 = _parse_fr_int(tokens, j)
        if rest is not None:
            return 100 + rest, j2
        return 100, j

    if t in _TENS:
        base = _TENS[t]
        j = i + 1
        if j < len(tokens) and tokens[j] == "et":
            j += 1
        if j < len(tokens) and tokens[j] in _UNITS_0_19:
            return base + _UNITS_0_19[tokens[j]], j + 1
        return base, j

    if t == "soixantedix":
        return 70, i + 1

    if t in {"quatrevingt", "quatrevingts"}:
        base = 80
        j = i + 1
        if j < len(tokens) and tokens[j] in _UNITS_0_19:
            v = _UNITS_0_19[tokens[j]]
            return base + v, j + 1
        return base, j

    if t == "quatre" and i + 1 < len(tokens) and tokens[i + 1] in {"vingt", "vingts"}:
        base = 80
        j = i + 2
        if j < len(tokens) and tokens[j] in _UNITS_0_19:
            v = _UNITS_0_19[tokens[j]]
            return base + v, j + 1
        return base, j

    if t in _UNITS_0_19 and i + 1 < len(tokens) and tokens[i + 1] in {"cent", "cents"}:
        hundreds = _UNITS_0_19[t] * 100
        j = i + 2
        rest, j2 = _parse_fr_int(tokens, j)
        if rest is not None:
            return hundreds + rest, j2
        return hundreds, j

    if t in _UNITS_0_19:
        return _UNITS_0_19[t], i + 1

    return None, i


def _parse_decimal_digits(tokens: List[str], i: int, max_len: int = 6) -> Tuple[str, int]:
    if i >= len(tokens):
        return "", i
    if tokens[i].isdigit():
        return tokens[i], i + 1

    def _parse_digit_sequence(tokens2: List[str], i2: int, max_len2: int) -> Tuple[str, int]:
        j2 = i2
        out: List[str] = []
        while j2 < len(tokens2) and len(out) < max_len2:
            t2 = tokens2[j2]
            if t2 in _DIGIT_WORDS:
                out.append(_DIGIT_WORDS[t2])
                j2 += 1
                continue
            if t2.isdigit() and len(t2) == 1:
                out.append(t2)
                j2 += 1
                continue
            break
        return "".join(out), j2

    j = i
    digits: List[str] = []
    while j < len(tokens) and sum(len(x) for x in digits) < max_len:
        remaining = max_len - sum(len(x) for x in digits)
        seq, j2 = _parse_digit_sequence(tokens, j, remaining)
        if len(seq) >= 2:
            digits.append(seq)
            j = j2
            continue

        v, j2 = _parse_fr_int(tokens, j)
        if v is not None and 0 <= v <= 999:
            s = str(v)
            if sum(len(x) for x in digits) + len(s) > max_len:
                break
            digits.append(s)
            j = j2
            continue

        t = tokens[j]
        if t in _DIGIT_WORDS and sum(len(x) for x in digits) + 1 <= max_len:
            digits.append(_DIGIT_WORDS[t])
            j += 1
            continue

        break

    return "".join(digits), j


def _parse_minutes(tokens: List[str], i: int) -> Tuple[Optional[float], int, Dict[str, Any]]:
    meta: Dict[str, Any] = {}

    whole, j = _parse_fr_int(tokens, i)
    if whole is None:
        return None, i, meta

    if j < len(tokens) and (tokens[j] in _DEC_SEP_WORDS or tokens[j] in {".", ","}):
        sep = tokens[j]
        j += 1
        dec_digits, j2 = _parse_decimal_digits(tokens, j)
        j = j2
        meta["decimal_sep"] = sep
        meta["decimal_raw"] = dec_digits
        if dec_digits:
            try:
                dec_val = int(dec_digits)
            except Exception:
                dec_val = 0
            scale = 10 ** len(dec_digits)
            return float(whole) + (dec_val / scale), j, meta

    return float(whole), j, meta


def _fmt_minutes(v: float) -> str:
    whole = int(v)
    dec = int(round((v - whole) * 1000))
    if dec >= 1000:
        whole += 1
        dec = 0
    return f"{whole:02d}.{dec:03d}"


def _fmt_ddm(lat_dir: str, lat_deg: int, lat_min: float, lon_dir: str, lon_deg: int, lon_min: float) -> str:
    return (
        f"{lat_dir} {lat_deg:02d}° {_fmt_minutes(lat_min)}' "
        f"{lon_dir} {lon_deg:03d}° {_fmt_minutes(lon_min)}'"
    )


def _find_candidates(tokens: List[str], max_candidates: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    for i in range(len(tokens)):
        t = tokens[i]
        if t not in _DIRECTION_LAT:
            continue

        lat_dir = _DIRECTION_LAT[t]
        j = _skip_connectors(tokens, i + 1)

        lat_deg, j2 = _parse_fr_int(tokens, j)
        if lat_deg is None:
            continue
        j = _skip_connectors(tokens, j2)

        if j < len(tokens) and tokens[j] in _DEG_WORDS:
            j = _skip_connectors(tokens, j + 1)

        lat_min, j2, lat_min_meta = _parse_minutes(tokens, j)
        if lat_min is None:
            continue
        j = _skip_connectors(tokens, j2)

        if j < len(tokens) and tokens[j] in _MIN_WORDS:
            j = _skip_connectors(tokens, j + 1)

        lon_dir = None
        lon_dir_token = None
        lon_pos = None
        for k in range(j, min(len(tokens), j + 10)):
            if tokens[k] in _DIRECTION_LON:
                lon_dir = _DIRECTION_LON[tokens[k]]
                lon_dir_token = tokens[k]
                lon_pos = k
                j = _skip_connectors(tokens, k + 1)
                break

        if lon_dir is None:
            continue

        lon_deg, j2 = _parse_fr_int(tokens, j)
        if lon_deg is None:
            continue
        j = _skip_connectors(tokens, j2)

        if j < len(tokens) and tokens[j] in _DEG_WORDS:
            j = _skip_connectors(tokens, j + 1)

        lon_min, j2, lon_min_meta = _parse_minutes(tokens, j)
        if lon_min is None:
            continue

        ddm = _fmt_ddm(lat_dir, lat_deg, lat_min, lon_dir, lon_deg, lon_min)

        candidates.append(
            {
                "id": f"fr_{len(candidates) + 1}",
                "text_output": ddm,
                "confidence": 0.6,
                "metadata": {
                    "language": "fr",
                    "span": {"start_token": i, "end_token": j2},
                    "lat": {
                        "dir_token": t,
                        "deg": lat_deg,
                        "min": lat_min,
                        "min_meta": lat_min_meta,
                    },
                    "lon": {
                        "dir_token": lon_dir_token,
                        "deg": lon_deg,
                        "min": lon_min,
                        "min_meta": lon_min_meta,
                    },
                },
            }
        )

        if len(candidates) >= max_candidates:
            break

    return candidates


class WrittenCoordsFrPlugin:
    def __init__(self):
        self.name = "written_coords_fr"
        self.description = "Reconnaissance de coordonnées GPS écrites en français"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()

        text = inputs.get("text", "")
        include_deconcat = bool(inputs.get("include_deconcat", True))
        max_candidates_raw = inputs.get("max_candidates", 20)

        try:
            max_candidates = int(max_candidates_raw)
        except Exception:
            max_candidates = 20

        if not text:
            return {
                "status": "success",
                "summary": "Aucun texte fourni",
                "results": [],
                "plugin_info": {
                    "name": self.name,
                    "version": "1.0.0",
                    "execution_time_ms": int((time.time() - start) * 1000),
                },
            }

        tokens = _tokenize_with_deconcat(text, include_deconcat=include_deconcat)
        candidates = _find_candidates(tokens, max_candidates=max_candidates)

        return {
            "status": "success",
            "summary": f"{len(candidates)} candidats FR",
            "results": candidates,
            "plugin_info": {
                "name": self.name,
                "version": "1.0.0",
                "execution_time_ms": int((time.time() - start) * 1000),
            },
        }


plugin = WrittenCoordsFrPlugin()


def execute(inputs):
    return plugin.execute(inputs)
