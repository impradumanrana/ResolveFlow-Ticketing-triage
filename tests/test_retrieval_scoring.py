"""Retrieval scoring and evaluation, offline.

The rerank formula is the MVP's, and these tests pin it. A weight change is a
retrieval change: it must fail here, be reasoned about, and be re-measured
against the labelled cases - not slip through because the suite still passes.

Behaviour that needs a live database - tenant scoping, archived exclusion,
and the measured comparison against the MVP - is verified in
`CLIENT_C05_TEST_REPORT.md` and reproducible via `scripts/compare_retrieval.py`.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.knowledge.embeddings import deterministic_embedding, index_dimensions
from app.knowledge.evaluation import (
    THRESHOLDS,
    LabelledCase,
    compare,
    evaluate,
    load_cases,
)
from app.knowledge.repository import build_or_tsquery
from app.knowledge.retrieval import (
    CORROBORATION_BONUS,
    MAX_SCORE,
    WEAK_SEMANTIC_FLOOR,
    WEIGHT_CATEGORY,
    WEIGHT_FUSION,
    WEIGHT_FUZZY,
    WEIGHT_KEYWORD,
    WEIGHT_LEXICAL,
    WEIGHT_SEMANTIC,
    bm25,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "app" / "fixtures" / "retrieval_cases.json"


# ---------------------------------------------------------------------------
# The preserved formula
# ---------------------------------------------------------------------------


def test_the_rerank_weights_match_the_proven_formula() -> None:
    """These are the MVP's weights. Changing one requires a fresh evaluation."""
    assert WEIGHT_SEMANTIC == 0.46
    assert WEIGHT_LEXICAL == 0.28
    assert WEIGHT_FUZZY == 0.08
    assert WEIGHT_KEYWORD == 0.08
    assert WEIGHT_CATEGORY == 0.06
    assert WEIGHT_FUSION == 0.04


def test_the_weights_form_a_convex_combination() -> None:
    total = (
        WEIGHT_SEMANTIC
        + WEIGHT_LEXICAL
        + WEIGHT_FUZZY
        + WEIGHT_KEYWORD
        + WEIGHT_CATEGORY
        + WEIGHT_FUSION
    )
    assert total == pytest.approx(1.0)


def test_semantic_similarity_carries_the_most_weight() -> None:
    assert WEIGHT_SEMANTIC > WEIGHT_LEXICAL > max(
        WEIGHT_FUZZY, WEIGHT_KEYWORD, WEIGHT_CATEGORY, WEIGHT_FUSION
    )


def test_a_score_can_never_be_presented_as_certainty() -> None:
    assert MAX_SCORE == 0.99
    assert CORROBORATION_BONUS == 0.05
    assert WEAK_SEMANTIC_FLOOR == 0.15


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


def test_bm25_rewards_a_matching_term() -> None:
    query = Counter(["password"])
    matching = bm25(query, ["password", "reset"], chunk_count=100, average_length=10,
                    document_frequency={"password": 5})
    missing = bm25(query, ["invoice", "refund"], chunk_count=100, average_length=10,
                   document_frequency={"password": 5})

    assert matching > 0
    assert missing == 0.0


def test_bm25_rewards_a_rarer_term_more() -> None:
    query = Counter(["password"])
    rare = bm25(query, ["password"], chunk_count=1000, average_length=10,
                document_frequency={"password": 2})
    common = bm25(query, ["password"], chunk_count=1000, average_length=10,
                  document_frequency={"password": 900})

    assert rare > common


def test_bm25_penalises_a_longer_document_for_the_same_match() -> None:
    query = Counter(["password"])
    short = bm25(query, ["password", "reset"], chunk_count=100, average_length=10,
                 document_frequency={"password": 5})
    long = bm25(query, ["password"] + ["filler"] * 200, chunk_count=100,
                average_length=10, document_frequency={"password": 5})

    assert short > long


def test_bm25_handles_an_unseen_term_without_dividing_by_zero() -> None:
    assert bm25(Counter(["novel"]), ["novel"], chunk_count=10, average_length=5,
                document_frequency={}) > 0


# ---------------------------------------------------------------------------
# Full-text query construction
# ---------------------------------------------------------------------------


def test_terms_are_ored_not_anded() -> None:
    """ANDing is why a natural-language question matched nothing at all."""
    assert build_or_tsquery(["find", "receipt", "purchase"]) == "find | receipt | purchase"


def test_duplicate_terms_collapse() -> None:
    assert build_or_tsquery(["password", "password", "reset"]) == "password | reset"


@pytest.mark.parametrize(
    "hostile",
    ["drop table", "a:*", "a&b", "a|b", "a!b", "(a)", "'quote'", "a\\b", ""],
)
def test_terms_that_cannot_be_validated_are_dropped_not_escaped(hostile: str) -> None:
    """A tsquery operator reaching the parser is a syntax error at best."""
    assert hostile not in build_or_tsquery([hostile, "safe"]).split(" | ")


def test_an_entirely_unusable_query_produces_no_tsquery() -> None:
    assert build_or_tsquery(["!!!", "&&&"]) == ""


# ---------------------------------------------------------------------------
# Deterministic embeddings
# ---------------------------------------------------------------------------


def test_the_offline_embedder_matches_the_stored_vector_width() -> None:
    """A narrower test vector would be rejected by the C04 column constraint."""
    assert len(deterministic_embedding("reset my password", index_dimensions())) == 1536


def test_the_offline_embedder_is_deterministic_and_normalised() -> None:
    first = deterministic_embedding("reset my password", 1536)
    second = deterministic_embedding("reset my password", 1536)

    assert first == second
    assert sum(value * value for value in first) == pytest.approx(1.0, abs=1e-9)


def test_the_offline_embedder_places_paraphrases_nearer_than_unrelated_text() -> None:
    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    query = deterministic_embedding("I forgot my password", 1536)
    near = deterministic_embedding("password reset help", 1536)
    far = deterministic_embedding("duplicate charge on my card", 1536)

    assert cosine(query, near) > cosine(query, far)


# ---------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------


def test_the_labelled_cases_reference_articles_that_exist() -> None:
    cases = load_cases(CASES_PATH)
    corpus = {
        article["article_id"]
        for article in json.loads((ROOT / "app" / "fixtures" / "faqs.json").read_text())
    }

    assert cases
    for case in cases:
        assert case.expected_ref in corpus, case.expected_ref


def test_a_perfect_run_scores_one_across_the_board() -> None:
    cases = [LabelledCase("q1", "A"), LabelledCase("q2", "B")]
    report = evaluate(cases, lambda case: [case.expected_ref], label="perfect")

    assert report.recall_at_1 == 1.0
    assert report.mrr == 1.0
    assert report.passed


def test_rank_two_halves_the_reciprocal_rank() -> None:
    cases = [LabelledCase("q", "A")]
    report = evaluate(cases, lambda case: ["Z", "A"], label="second")

    assert report.recall_at_1 == 0.0
    assert report.recall_at_k == 1.0
    assert report.mrr == pytest.approx(0.5)


def test_a_complete_miss_scores_zero_and_is_listed() -> None:
    cases = [LabelledCase("q", "A")]
    report = evaluate(cases, lambda case: ["X", "Y", "Z"], label="miss")

    assert report.mrr == 0.0
    assert not report.passed
    assert len(report.misses) == 1
    assert "miss:" in report.describe()


def test_a_regression_is_labelled_in_the_comparison() -> None:
    """A silent quality drop is the one failure this phase must not allow."""
    cases = [LabelledCase("q", "A")]
    before = evaluate(cases, lambda case: ["A"], label="MVP")
    after = evaluate(cases, lambda case: ["Z", "A"], label="pgvector")

    assert "REGRESSION" in compare(before, after)


def test_an_improvement_is_not_labelled_a_regression() -> None:
    cases = [LabelledCase("q", "A")]
    before = evaluate(cases, lambda case: ["Z", "A"], label="MVP")
    after = evaluate(cases, lambda case: ["A"], label="pgvector")

    assert "REGRESSION" not in compare(before, after)


def test_thresholds_are_versioned_and_demanding() -> None:
    assert THRESHOLDS["recall_at_1"] >= 0.8
    assert THRESHOLDS["recall_at_3"] >= 0.9
    assert THRESHOLDS["mrr"] >= 0.85
