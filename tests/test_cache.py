from reliability_lab.cache import ResponseCache


def test_response_cache_exact_hit() -> None:
    cache = ResponseCache(ttl_seconds=60, similarity_threshold=0.92)
    cache.set("Explain circuit breaker states", "cached answer")

    cached, score = cache.get("Explain circuit breaker states")

    assert cached == "cached answer"
    assert score == 1.0


def test_response_cache_skips_privacy_queries() -> None:
    cache = ResponseCache(ttl_seconds=60, similarity_threshold=0.1)
    cache.set("Give me the current account balance for user 123", "private answer")

    cached, score = cache.get("Give me the current account balance for user 123")

    assert cached is None
    assert score == 0.0


def test_response_cache_rejects_different_year_false_hit() -> None:
    cache = ResponseCache(ttl_seconds=60, similarity_threshold=0.3)
    cache.set("refund policy for 2024", "old policy")

    cached, score = cache.get("refund policy for 2026")

    assert cached is None
    assert score >= 0.3
    assert cache.false_hit_log
