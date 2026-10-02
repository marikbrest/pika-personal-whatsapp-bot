"""
cosine_similarity() is a pure function with no network/DB dependency, and
semantic search's entire ranking quality rests on it being correct - the
cheapest, highest-value place in the embeddings pipeline to test thoroughly.
"""
import math
from array import array

import pytest

from src.integrations.embeddings import cosine_similarity, embed_text


def _vec(*floats: float) -> bytes:
    return array("f", floats).tobytes()


def test_identical_vectors_have_similarity_one():
    v = _vec(1.0, 2.0, 3.0)
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-5)


def test_opposite_vectors_have_similarity_negative_one():
    a = _vec(1.0, 0.0)
    b = _vec(-1.0, 0.0)
    assert cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-5)


def test_orthogonal_vectors_have_similarity_zero():
    a = _vec(1.0, 0.0)
    b = _vec(0.0, 1.0)
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-5)


def test_scale_invariant():
    """Cosine similarity measures direction, not magnitude - a vector scaled
    by any positive factor must compare identically to the original."""
    a = _vec(1.0, 2.0, 3.0)
    b = _vec(2.0, 4.0, 6.0)  # a * 2
    assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-5)


def test_zero_vector_returns_zero_not_a_division_error():
    zero = _vec(0.0, 0.0, 0.0)
    nonzero = _vec(1.0, 1.0, 1.0)
    assert cosine_similarity(zero, nonzero) == 0.0
    assert cosine_similarity(zero, zero) == 0.0


def test_matches_manual_calculation():
    a = _vec(1.0, 2.0, 3.0)
    b = _vec(4.0, 5.0, 6.0)
    dot = 1 * 4 + 2 * 5 + 3 * 6
    norm_a = math.sqrt(1 * 1 + 2 * 2 + 3 * 3)
    norm_b = math.sqrt(4 * 4 + 5 * 5 + 6 * 6)
    expected = dot / (norm_a * norm_b)
    assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)


def test_embed_text_serializes_the_embedding_api_result_as_float32(monkeypatch):
    import src.integrations.embeddings as embeddings

    monkeypatch.setattr(embeddings, "embed_content", lambda text: [0.5, -0.25, 1.0])
    result = embed_text("some text")

    va = array("f")
    va.frombytes(result)
    assert list(va) == pytest.approx([0.5, -0.25, 1.0], abs=1e-6)
