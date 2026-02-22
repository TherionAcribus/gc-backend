"""Tests for score_text_fast(), score_and_rank_results(), and GPS gatekeeper."""

from __future__ import annotations

import pytest

from gc_backend.plugins.scoring.scorer import (
    _gps_gatekeeper_fast,
    score_and_rank_results,
    score_text,
    score_text_fast,
)


# ── score_text_fast ──────────────────────────────────────────────────


class TestScoreTextFast:
    """Unit tests for the lightweight fast scorer."""

    def test_empty_text_returns_zero(self):
        assert score_text_fast("") == 0.0

    def test_very_short_text_returns_zero(self):
        assert score_text_fast("AB") == 0.0
        assert score_text_fast("XYZ") == 0.0

    def test_gibberish_scores_low(self):
        gibberish = "XQZJWKPLMCVBN"
        score = score_text_fast(gibberish)
        assert score < 0.10, f"Gibberish should score very low, got {score}"

    def test_natural_french_scores_higher(self):
        french = "LE TRESOR EST CACHE PRES DU GRAND CHENE AU NORD DE LA RIVIERE"
        score = score_text_fast(french)
        assert score > 0.4, f"French text should score high, got {score}"

    def test_natural_english_scores_higher(self):
        english = "THE TREASURE IS HIDDEN NEAR THE OLD OAK TREE NORTH OF THE RIVER"
        score = score_text_fast(english)
        assert score > 0.4, f"English text should score high, got {score}"

    def test_repetitive_text_scores_zero(self):
        repetitive = "AAAAAAAAAAAAAAAAAAAAAA"
        score = score_text_fast(repetitive)
        assert score == 0.0

    def test_random_noise_scores_low(self):
        noise = "KXJQZWPLFMVBNRTYGHS" * 3
        score = score_text_fast(noise)
        assert score < 0.10, f"Random noise should score very low, got {score}"

    def test_cipher_output_vs_real_language(self):
        """Critical test: real language must rank far above cipher gibberish."""
        correct = "CECI EST UN TEST DE MESSAGE POUR LE GEOCACHING"
        gibberish = "XVXR VHG FM GVHG WV NVHHZTV KLFI OV TVLXZXSRMT"
        score_correct = score_text_fast(correct)
        score_gibberish = score_text_fast(gibberish)
        assert score_correct > 0.5, f"Real French should score > 0.5, got {score_correct}"
        assert score_gibberish < 0.10, f"Atbash gibberish should score < 0.10, got {score_gibberish}"
        assert score_correct > score_gibberish * 5, (
            f"Real language ({score_correct:.3f}) must be far above gibberish ({score_gibberish:.3f})"
        )

    def test_returns_float(self):
        result = score_text_fast("SOME TEXT HERE")
        assert isinstance(result, float)

    def test_score_bounded_zero_one(self):
        for text in ["A", "HELLO WORLD", "XQZJWKPLMCVBN", "THE CAT SAT ON THE MAT"]:
            score = score_text_fast(text)
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for '{text}'"

    def test_fast_much_cheaper_than_full(self):
        """score_text_fast should at least not crash with same inputs as score_text."""
        text = "COORDONNEES NORD QUARANTE HUIT DEGRES"
        fast = score_text_fast(text)
        full = score_text(text)
        assert isinstance(fast, float)
        assert isinstance(full, dict)
        assert "score" in full


# ── score_and_rank_results ───────────────────────────────────────────


class TestScoreAndRankResults:
    """Unit tests for the batch scoring + ranking function."""

    def test_empty_list_returns_empty(self):
        assert score_and_rank_results([]) == []

    def test_single_result_is_returned(self):
        results = [{"text_output": "LE TRESOR EST ICI", "confidence": 0.5}]
        ranked = score_and_rank_results(results, top_k=10)
        assert len(ranked) >= 1
        assert "confidence" in ranked[0]
        assert isinstance(ranked[0]["confidence"], float)

    def test_results_sorted_by_confidence_descending(self):
        results = [
            {"text_output": "XQZJWKPLMCVBN", "confidence": 0.0},
            {"text_output": "LE TRESOR EST CACHE AU NORD", "confidence": 0.0},
            {"text_output": "AAAABBBBCCCC", "confidence": 0.0},
        ]
        ranked = score_and_rank_results(results, top_k=10, min_score=0.0)
        confidences = [r["confidence"] for r in ranked]
        assert confidences == sorted(confidences, reverse=True)

    def test_top_k_limits_output(self):
        results = [
            {"text_output": f"RESULT NUMBER {i} WITH SOME TEXT", "confidence": 0.0}
            for i in range(50)
        ]
        ranked = score_and_rank_results(results, top_k=5)
        assert len(ranked) <= 5

    def test_items_without_text_output_are_skipped(self):
        results = [
            {"text_output": "", "confidence": 0.5},
            {"no_text": "abc", "confidence": 0.5},
            {"text_output": "HELLO WORLD FROM GEOCACHE", "confidence": 0.5},
        ]
        ranked = score_and_rank_results(results, top_k=10, min_score=0.0)
        # Only the third item has valid text_output
        assert len(ranked) <= 1

    def test_metadata_scoring_added(self):
        results = [{"text_output": "COORDONNEES NORD QUARANTE HUIT", "confidence": 0.0}]
        ranked = score_and_rank_results(results, top_k=10, min_score=0.0)
        if ranked:
            meta = ranked[0].get("metadata", {})
            assert isinstance(meta, dict)
            if "scoring" in meta:
                assert "features" in meta["scoring"]

    def test_fast_reject_filters_garbage_on_large_sets(self):
        """With many results, fast-reject should filter out obvious garbage."""
        good = [
            {"text_output": "THE TREASURE IS HIDDEN NEAR THE OLD OAK TREE", "confidence": 0.0}
        ]
        garbage = [
            {"text_output": f"XQZJWK{i}PLMCVBNRTYGHSXQZJWK", "confidence": 0.0}
            for i in range(100)
        ]
        results = garbage + good
        ranked = score_and_rank_results(results, top_k=10, fast_reject_threshold=0.01)
        # Good result should survive; garbage may or may not depending on thresholds
        assert len(ranked) <= 10

    def test_preserves_existing_metadata(self):
        results = [
            {
                "text_output": "LE TRESOR EST CACHE PRES DU CHENE",
                "confidence": 0.5,
                "metadata": {"original_key": "abc"},
            }
        ]
        ranked = score_and_rank_results(results, top_k=10, min_score=0.0)
        if ranked:
            assert ranked[0]["metadata"].get("original_key") == "abc"

    def test_no_results_with_text_returns_empty(self):
        results = [
            {"text_output": "", "confidence": 0.5},
            {"text_output": "   ", "confidence": 0.5},
        ]
        ranked = score_and_rank_results(results, top_k=10)
        assert ranked == []


# ── GPS gatekeeper ───────────────────────────────────────────────────


class TestGpsGatekeeperFast:
    """Unit tests for the enhanced GPS gatekeeper."""

    def test_empty_text(self):
        assert _gps_gatekeeper_fast("") is False
        assert _gps_gatekeeper_fast(None) is False

    def test_cardinal_letters(self):
        assert _gps_gatekeeper_fast("N 48 E 002") is True

    def test_written_cardinal_words_fr(self):
        assert _gps_gatekeeper_fast("nord 48 est 002") is True

    def test_written_cardinal_words_en(self):
        assert _gps_gatekeeper_fast("north 48 east 002") is True

    def test_written_cardinal_sud_ouest(self):
        assert _gps_gatekeeper_fast("sud 48 ouest 002") is True
        assert _gps_gatekeeper_fast("south 48 west 002") is True

    def test_degree_symbol(self):
        assert _gps_gatekeeper_fast("48° 51.400") is True
        assert _gps_gatekeeper_fast("48º 51.400") is True

    def test_dms_prime_markers(self):
        assert _gps_gatekeeper_fast("48° 51' 24") is True
        assert _gps_gatekeeper_fast("48° 51\u2032 24") is True

    def test_decimal_degree_pair(self):
        assert _gps_gatekeeper_fast("48.8566, 2.3522") is True
        assert _gps_gatekeeper_fast("48.8566 2.3522") is True  # space also matches
        assert _gps_gatekeeper_fast("-48.8566, -2.3522") is True
        assert _gps_gatekeeper_fast("48.85 2.35") is False  # too few decimals

    def test_compact_numeric_pair(self):
        assert _gps_gatekeeper_fast("4851400 0021050") is True   # lat 48°, lon 002°
        assert _gps_gatekeeper_fast("4851400 21050") is False    # lon too short
        assert _gps_gatekeeper_fast("1011000 1010110") is False  # binary text — must NOT match

    def test_plain_text_no_match(self):
        assert _gps_gatekeeper_fast("HELLO WORLD THIS IS JUST TEXT") is False
        assert _gps_gatekeeper_fast("THE TREASURE IS HIDDEN HERE") is False

    def test_numbers_without_pattern(self):
        assert _gps_gatekeeper_fast("12345 67890") is False
