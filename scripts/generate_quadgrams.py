#!/usr/bin/env python3
"""Generate quadgram log-probability tables for the scoring system.

Two modes:
  1. --from-bigrams  (default)  Use embedded bigram transition matrices to
     synthesize approximate quadgram frequencies via a Markov chain.  This is
     fast and requires no external data.  Good enough for distinguishing real
     text from gibberish.
  2. --from-corpus <file>       Count quadgrams in a UTF-8 text file for
     higher accuracy.  You can feed any large plain-text corpus.

Output: JSON files in gc_backend/plugins/scoring/resources/quadgrams/<lang>.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict

RESOURCES_DIR = (
    Path(__file__).resolve().parent.parent
    / "gc_backend"
    / "plugins"
    / "scoring"
    / "resources"
    / "quadgrams"
)

# ──────────────────────────────────────────────────────────────────────
# Bigram transition matrices  (row = preceding letter, col = following)
# Values are approximate relative frequencies (will be normalised).
# Sources: published bigram tables for each language.
# ──────────────────────────────────────────────────────────────────────

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
L2I = {ch: i for i, ch in enumerate(LETTERS)}

def _matrix(flat: str) -> list[list[float]]:
    """Parse a 26×26 flat string of space-separated floats."""
    vals = [float(x) for x in flat.split()]
    assert len(vals) == 676, f"Expected 676 values, got {len(vals)}"
    return [vals[i * 26 : (i + 1) * 26] for i in range(26)]


# fmt: off
# English bigram transition frequencies (×10000, approximate)
_EN_BIGRAMS = (
    # A     B     C     D     E     F     G     H     I     J     K     L     M     N     O     P     Q     R     S     T     U     V     W     X     Y     Z
    "  2   56  100   91    5   24   36    5   47    1   20  175   67  373    2   43    2  172  167  365   24   28   29    2   30    1 "  # A
    "  42    3    1    1  100    0    0    0   37    2    0   52    1    1   55    0    0   24    8    2   42    1    0    0   19    0 "  # B
    "  97    1   14    1  106    0    0   61  61    0   28   31    1    1   99    0    0   24    7   75   29    0    0    0    8    0 "  # C
    " 48    6    3   10  100    5    6   10   65    2    1   11    8    8   55    2    0   14   24    8   16    3    5    0   10    0 "  # D
    " 82   11   52   96   45   26    8   10    8    1    2   57   36  153   31   23    5  207   99    5    3   25   23    8   11    1 "  # E
    "  40    2    1    2   34   18    1    2   39    0    1   14    2    1   63    2    0   29    4   28   13    0    2    0    3    0 "  # F
    "  41    3    1    2   55    3    6   32   37    0    0   18    4    6   42    1    0   26    9    3   16    0    4    0    2    0 "  # G
    " 167    2    1    2  316    2    1    1  163    0    1    3    4    3   96    1    0   15    4    9    9    0    5    0    5    0 "  # H
    "  32   10   70   44   55   18   27    0    1    0    4   79   48  294   64    4    0   42  163  172    0   24    0    4    0   10 "  # I
    "  10    0    0    0   15    0    0    0    5    0    0    0    0    0   10    0    0    0    0    0   10    0    0    0    0    0 "  # J
    "  18    2    1    1   79    3    1    3   43    0    1    5    2   31    8    1    0    1   12    4    1    0    4    0    3    0 "  # K
    "  74    2    2   46  120    8    1    1   83    0    4   75    5    2   60    5    0    2   12    7   14    3    3    0   40    0 "  # L
    "  95    9    1    1  105    1    0    1   40    0    0    1   10    5   46   12    0    2    8    1   15    0    1    0   12    0 "  # M
    "  56   4   42  137  100    6   87    6   56    1   12    2    1    8   79    2    1    1   35  181    8    4   10    0   10    0 "  # N
    "  10   12   12   12    5   84   10    5    5    0   10   28   40  203   20   17    0  132   22   47   94   12   28    2    4    0 "  # O
    "  54    1    0    1   60    1    0   12   20    0    0   26    2    1   37   12    0   55    7   12   17    0    1    0    2    0 "  # P
    "  1    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0   48    0    0    0    0    0 "  # Q
    " 76    4    8   12  182    5    8    3  79    0    8    5   12    7   79    4    0   11   38    4   12    2    4    0   20    0 "  # R
    "  46    5   11    3  100    7    1   69  61    1    4    8   10    5   52   28    2    1   45  164   22    0   14    0    5    0 "  # S
    "  52    5    4    3   78    4    1  262   93    0    2    8    4    2  147    3    0   21   12    4   17    0   12    0    5    0 "  # T
    "  10    14   28   15   14    4   23    1   21    0    2   55   20  115    4   20    0  102   64  107    0    1    1    1    1    0 "  # U
    "  21    0    0    0   95    0    0    0   30    0    0    1    0    0   11    0    0    2    1    1    1    0    0    0    2    0 "  # V
    "  51    1    1    2   34    1    0   46   56    0    1    1    1   18   33    1    0    4    3    3    1    0    1    0    1    0 "  # W
    "   6    0    3    0    6    1    0    1   10    0    0    1    1    0    3    8    0    0    2    7    1    0    1    0    1    0 "  # X
    "  12    5    6    3   12    5    1    3   12    0    0    3    7    3   33    5    0    2   17    8    0    0    6    0    0    0 "  # Y
    "   5    0    0    0   10    0    0    0    3    0    0    0    0    0    3    0    0    0    0    0    0    0    0    0    1    0 "  # Z
)

_FR_BIGRAMS = (
    # A     B     C     D     E     F     G     H     I     J     K     L     M     N     O     P     Q     R     S     T     U     V     W     X     Y     Z
    "  5   30   58   36   12   14   30    2  100    3    1  107   54  231    3   37    5  118  120  113   62   38    1   10   15    1 "  # A
    "  26    1    0    0   28    0    0    0    9    1    0   18    1    1   17    0    0   20    4    1    5    0    0    0    1    0 "  # B
    "  47    1    4    1   65    0    0   24  20    0    3   10    1    1   67    1    1   10    2   22   14    0    0    0    2    0 "  # C
    "  32    1    1    3  103    1    1    1   44    1    0    2    2    2   24    2    0   13    4    1   22    1    0    0    2    0 "  # D
    " 31   12   42   38   30   12    6    1    8    3    0   57   38  170   11   29   10  105  143   48   32   17    0   14   10    3 "  # E
    "  33    1    1    1   28    8    0    1   23    0    0    5    1    1   27    1    0   14    2    1    6    0    0    0    1    0 "  # F
    "  25    1    1    1   30    0    2    1   10    0    0   10    2   14   10    1    0   14    2    1   10    0    0    0    1    0 "  # G
    "  20    1    1    1   30    0    0    1    8    0    0    2    1    1   15    1    0    2    1    1    5    0    0    0    2    0 "  # H
    "  30   10   28   16   72    8   12    1    4    1    1   54   18  133   53   12    7   35   76  80    4   12    0    3    1    3 "  # I
    "  10    0    0    0   18    0    0    0    3    0    0    0    0    0   14    0    0    0    0    0   10    0    0    0    0    0 "  # J
    "   4    0    0    0    3    0    0    0    2    0    0    0    0    0    1    0    0    0    1    0    0    0    0    0    0    0 "  # K
    "  80    3    3   11  135    3    1    1   42    1    0   39    3    2   30    5    2    1    7   10   30    2    0    0    3    0 "  # L
    "  50    5    2    1   88    1    0    1   18    0    0    1   12    4   24   10    0    1    3    2    8    0    0    0    2    0 "  # M
    " 33    3   30   24  101    7   13    2   25    1    1    2    2   12   32    4    3    1   43   80    8    3    0    0    1    1 "  # N
    "  3   10   14    8    5    5    5    1   23    1    1   19   25  178   7   16    1  70    18   18   89   10    0    2    3    0 "  # O
    "  41    1    1    1   36    1    1    7   14    0    0   20    1    1   28    8    0   42    4    4   12    0    0    0    2    0 "  # P
    "  1    0    0    0    1    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0   48    0    0    0    0    0 "  # Q
    " 52    4    7   11  116    3    4    2   42    1    1    5    5    5   36    7    1    8   20   19   13    4    0    0    4    0 "  # R
    "  32    3    6    5   78    3    1    3   36    1    0    4    4    3   36   17    3    2   44   52   22    1    0    0    3    0 "  # S
    "  49    3    2    2   88    3    1    6   68    1    0    5    3    3   37    3    1   40   14    8   22    1    0    0    3    0 "  # T
    "  8   10   18    16  47    4    5    1   14    1    1   26   10   43    3    10    1   74   42   38    2   10    0   10    1    1 "  # U
    "  32    1    0    0   45    0    0    0   27    0    0    1    0    0   12    1    0   10    1    1    3    0    0    0    1    0 "  # V
    "   1    0    0    0    1    0    0    0    1    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0 "  # W
    "   4    0    2    0    4    0    0    0    8    0    0    0    0    0    1    4    0    0    1    4    1    0    0    0    1    0 "  # X
    "   8    1    3    2   10    1    1    1    2    0    0    2    3    2    5    4    0    2   10    2    1    1    0    0    0    0 "  # Y
    "   3    0    0    0    5    0    0    0    1    0    0    0    0    0    2    0    0    0    0    0    0    0    0    0    0    0 "  # Z
)

_DE_BIGRAMS = (
    # A     B     C     D     E     F     G     H     I     J     K     L     M     N     O     P     Q     R     S     T     U     V     W     X     Y     Z
    "  4   30   35   25   10   18   30    8   15    2    2   60   28  160    2   14    0   68  80   65   40   10    4    0    1    3 "  # A
    "  32    2    0    1   72    1    1    1   22    1    1   10    1    2   12    1    0   14    6    4   12    1    2    0    1    0 "  # B
    "  8    0    0    0    8    0    0   80   4    0   14    1    0    0    3    0    0    0    2    0    2    0    0    0    0    0 "  # C
    "  42    3    1    4  112    3    1    2   56    1    1    4    3    3   18    2    0   14    6    2   18    2    3    0    1    0 "  # D
    " 20   18   12   30   10   10   18    8   90    1    4   28   12  180    2   10    1  158  58   18    6   10    8    1    1    4 "  # E
    "  25    2    0    2   26    6    1    1   12    1    1    6    2    2   12    1    0   14   4   14   14    1    2    0    0    0 "  # F
    "  18    3    0    2   60    2    4    2   10    0    1   10    2    2    6    1    0   18   12   10   10    1    2    0    0    1 "  # G
    "  40    2    0    1  100    2    1    1   38    0    1   20    4    8   18    1    0   22   10   24    8    1    4    0    1    0 "  # H
    "  8   8   38   18   68    6   20    2    2    1    4   18   14   72   8    2    0   12   44   52    2    6    2    0    0    4 "  # I
    "  12    0    0    0   12    0    0    0    1    0    0    0    0    0    6    0    0    0    0    0    6    0    0    0    0    0 "  # J
    "  18    2    1    1   42    2    1    2   10    0    4   10    2    2   18    1    0   10    4   18    8    1    2    0    1    0 "  # K
    "  30    6    2   10   50    4    4    2   32    0    2   18    4    2   14    2    0    2   14   14   10    2    2    0    1    0 "  # L
    "  36    4    0    2   44    2    1    1   18    0    1    2   12    3   10    4    0    2    6    2   12    1    2    0    0    0 "  # M
    "  26    4    4   42   68    4   38   4   30    1    6    4    2   14   12    2    0    2   28   24   14    4    6    0    0    6 "  # N
    "  2   10   10    8    6    4    4    2    4    1    2   18   14   42  10    8    0   32   10    4    4    4    4    1    0    0 "  # O
    "  14    1    0    0   14    4    1    4    8    0    1    6    1    1   10    4    0   16    2    2    4    0    2    0    0    0 "  # P
    "   1    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0   10    0    0    0    0    0 "  # Q
    "  36    6    4   18   68    6    6    4   32    1    6   6    4   10   14    2    0    4   14   18   18    4    6    0    1    2 "  # R
    "  14    4   32    4   48    2    2   10   32    1    4    4    2    2   12   10    0    2   26   48    6    2    4    0    0    2 "  # S
    "  22    4    2    2  100    4    4   14   38    1    2   10    4    2   14    2    0   20   18   14   18    4    6    0    1   12 "  # T
    "  4   10   12    8   16    10   10    2    2    1    2   10    12   80    2    6    0   28   30   18    2    2    4    0    0    2 "  # U
    "  6    0    0    0   42    0    0    0   14    0    0    1    0    0   20    0    0    2    0    0    0    2    0    0    0    0 "  # V
    "  28    0    0    0   22    0    0    0   26    0    0    0    0    0   12    0    0    2    2    0   10    0    2    0    0    0 "  # W
    "   2    0    0    0    2    0    0    0    4    0    0    0    0    0    0    2    0    0    0    4    0    0    0    0    0    0 "  # X
    "   0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    0    2    0    0    0    0    0    0    0 "  # Y
    "  4    1    0    0   10    0    0    0    4    0    0    0    0    0    2    0    0    0    0    1   10    0    2    0    0    2 "  # Z
)
# fmt: on

# Unigram frequencies (relative)
_UNIGRAMS = {
    "en": {
        "A": 8167, "B": 1492, "C": 2782, "D": 4253, "E": 12702, "F": 2228,
        "G": 2015, "H": 6094, "I": 6966, "J": 153, "K": 772, "L": 4025,
        "M": 2406, "N": 6749, "O": 7507, "P": 1929, "Q": 95, "R": 5987,
        "S": 6327, "T": 9056, "U": 2758, "V": 978, "W": 2360, "X": 150,
        "Y": 1974, "Z": 74,
    },
    "fr": {
        "A": 8110, "B": 901, "C": 3260, "D": 3669, "E": 17540, "F": 1066,
        "G": 866, "H": 737, "I": 7529, "J": 613, "K": 49, "L": 5456,
        "M": 2968, "N": 7095, "O": 5796, "P": 2521, "Q": 1362, "R": 6553,
        "S": 7948, "T": 7244, "U": 6311, "V": 1838, "W": 49, "X": 427,
        "Y": 308, "Z": 136,
    },
    "de": {
        "A": 6516, "B": 1886, "C": 2732, "D": 5076, "E": 16396, "F": 1656,
        "G": 3009, "H": 4577, "I": 6550, "J": 268, "K": 1417, "L": 3437,
        "M": 2534, "N": 9776, "O": 2594, "P": 670, "Q": 18, "R": 7003,
        "S": 7270, "T": 6154, "U": 4166, "V": 846, "W": 1921, "X": 34,
        "Y": 39, "Z": 1134,
    },
}

BIGRAM_DATA = {
    "en": _EN_BIGRAMS,
    "fr": _FR_BIGRAMS,
    "de": _DE_BIGRAMS,
}


def _normalize_matrix(raw: list[list[float]]) -> list[list[float]]:
    """Row-normalise so each row sums to 1."""
    out = []
    for row in raw:
        s = sum(row)
        if s <= 0:
            out.append([1.0 / 26] * 26)
        else:
            out.append([v / s for v in row])
    return out


def _normalize_unigrams(raw: Dict[str, int]) -> list[float]:
    total = sum(raw.values())
    return [raw.get(ch, 1) / total for ch in LETTERS]


def generate_from_bigrams(lang: str, min_log_prob: float = -7.0) -> Dict[str, float]:
    """Generate quadgram log10-probs using bigram Markov chain."""
    raw_bigrams = _matrix(BIGRAM_DATA[lang])
    trans = _normalize_matrix(raw_bigrams)
    unigrams = _normalize_unigrams(_UNIGRAMS[lang])

    quadgrams: Dict[str, float] = {}
    for a in range(26):
        pa = unigrams[a]
        for b in range(26):
            pab = pa * trans[a][b]
            if pab < 1e-12:
                continue
            for c in range(26):
                pabc = pab * trans[b][c]
                if pabc < 1e-12:
                    continue
                for d in range(26):
                    pabcd = pabc * trans[c][d]
                    if pabcd < 1e-15:
                        continue
                    logp = math.log10(pabcd) if pabcd > 0 else -10.0
                    if logp >= min_log_prob:
                        qg = LETTERS[a] + LETTERS[b] + LETTERS[c] + LETTERS[d]
                        quadgrams[qg] = round(logp, 4)

    return quadgrams


def generate_from_corpus(filepath: str, min_count: int = 2) -> Dict[str, float]:
    """Count quadgrams from a UTF-8 text file."""
    text = Path(filepath).read_text(encoding="utf-8")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = re.sub(r"[^A-Z]", "", text)

    counts: Dict[str, int] = {}
    for i in range(len(text) - 3):
        qg = text[i : i + 4]
        counts[qg] = counts.get(qg, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return {}

    quadgrams: Dict[str, float] = {}
    for qg, cnt in counts.items():
        if cnt >= min_count:
            quadgrams[qg] = round(math.log10(cnt / total), 4)

    return quadgrams


def main():
    parser = argparse.ArgumentParser(description="Generate quadgram tables for scoring")
    parser.add_argument(
        "--langs",
        nargs="+",
        default=["en", "fr", "de"],
        help="Languages to generate (default: en fr de)",
    )
    parser.add_argument(
        "--from-corpus",
        type=str,
        default=None,
        help="Path to a UTF-8 text corpus (overrides bigram mode)",
    )
    parser.add_argument(
        "--corpus-lang",
        type=str,
        default="en",
        help="Language code when using --from-corpus",
    )
    parser.add_argument(
        "--min-log-prob",
        type=float,
        default=-7.0,
        help="Minimum log10 probability to keep (default: -7.0)",
    )
    args = parser.parse_args()

    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)

    if args.from_corpus:
        lang = args.corpus_lang
        print(f"Generating {lang} quadgrams from corpus: {args.from_corpus}")
        quadgrams = generate_from_corpus(args.from_corpus)
        outpath = RESOURCES_DIR / f"{lang}.json"
        outpath.write_text(json.dumps(quadgrams, indent=None, separators=(",", ":")), encoding="utf-8")
        print(f"  → {outpath}: {len(quadgrams)} quadgrams ({outpath.stat().st_size / 1024:.1f} KB)")
    else:
        for lang in args.langs:
            if lang not in BIGRAM_DATA:
                print(f"No bigram data for '{lang}', skipping.")
                continue
            print(f"Generating {lang} quadgrams from bigram chain...")
            quadgrams = generate_from_bigrams(lang, min_log_prob=args.min_log_prob)
            # Sort by log-prob descending for readability
            sorted_qg = dict(sorted(quadgrams.items(), key=lambda x: x[1], reverse=True))
            outpath = RESOURCES_DIR / f"{lang}.json"
            outpath.write_text(
                json.dumps(sorted_qg, indent=None, separators=(",", ":")),
                encoding="utf-8",
            )
            print(f"  → {outpath}: {len(sorted_qg)} quadgrams ({outpath.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
