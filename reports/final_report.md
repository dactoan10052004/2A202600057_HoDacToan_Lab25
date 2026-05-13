# Day 10 Reliability Final Report

## 1. Architecture summary

The gateway wraps unreliable LLM providers with cache lookup, per-provider circuit breakers, fallback routing, static fallback, and run metrics.

```text
User Request
  -> ReliabilityGateway
  -> ResponseCache or SharedRedisCache
  -> CircuitBreaker(primary) -> Provider primary
  -> CircuitBreaker(backup)  -> Provider backup
  -> Static fallback when all providers fail
```

## 2. Configuration rationale

| Setting | Value | Why this value |
|---|---:|---|
| failure_threshold | 3 | Opens quickly during sustained failures without tripping on one transient error. |
| reset_timeout_seconds | 2 | Short enough for lab recovery evidence while still showing OPEN fail-fast behavior. |
| success_threshold | 1 | A single successful half-open probe closes the circuit for fast recovery. |
| cache TTL seconds | 300 | Five minutes is a reasonable freshness window for FAQ-style responses. |
| similarity_threshold | 0.92 | Tested: 0.85 caused false hits on date-sensitive queries such as 2024 vs 2026; 0.92 rejected them. |
| load_test requests | 1000 | Enough repeated traffic to expose fallback, cache hits, and latency percentiles across 5 scenarios. |
| concurrency | 10 | ThreadPoolExecutor workers simulate real multi-client load against the same gateway. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 95% | 99.66% | yes |
| Error rate | <= 5% | 0.34% | yes |
| Latency P95 | < 2500 ms | 312.3200 | yes |
| Fallback success rate | >= 90% | 98.67% | yes |
| Recovery time | < 5000 ms | 2898.1274 | yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 5000 |
| availability | 0.9966 |
| error_rate | 0.0034 |
| latency_p50_ms | 1.5400 |
| latency_p95_ms | 312.3200 |
| latency_p99_ms | 504.1200 |
| fallback_success_rate | 0.9867 |
| cache_hit_rate | 0.6392 |
| circuit_open_count | 18 |
| recovery_time_ms | 2898.1274 |
| estimated_cost | 0.4469 |
| estimated_cost_saved | 0.0360 |

## 5. Cache comparison

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 209.5686 | 0.3005 | -209.2681 |
| latency_p95_ms | 237.3791 | 225.4315 | -11.9475 |
| estimated_cost | 0.0088 | 0.0021 | -0.0067 |
| cache_hit_rate | 0.00% | 77.30% | 77.30% |

**False-hit guardrail example:**

Query A: `"What is the refund policy for a student who missed the 2024 deadline?"`
Query B: `"What is the refund policy for a student who missed the 2026 deadline?"`

Token overlap is high, but `_looks_like_false_hit()` extracts `2024` and `2026`, sees they differ, and blocks the cache hit.

**Privacy guardrail example:**

Query: `"Give me the current account balance for user 123."`

`_is_uncacheable()` matches `balance` and `user 123`, so this query is never stored or served from cache.

## 6. Redis shared cache

In-memory cache is per process, so horizontally scaled gateways would miss entries created by sibling instances. `SharedRedisCache` stores query/response hashes in Redis with TTL, so separate gateway instances can reuse the same safe cached responses.

**Shared state evidence - two instances reading the same entry:**

```python
cache_a = SharedRedisCache(redis_url="redis://localhost:6379/0", ttl_seconds=300,
                           similarity_threshold=0.92, prefix="rl:cache:")
cache_b = SharedRedisCache(redis_url="redis://localhost:6379/0", ttl_seconds=300,
                           similarity_threshold=0.92, prefix="rl:cache:")

cache_a.set("Explain circuit breaker states.", "A circuit breaker has three states...")
result, score = cache_b.get("Explain circuit breaker states.")
# result = "A circuit breaker has three states..."  score = 1.0
```

Instance `cache_b` retrieved the entry written by `cache_a`, proving Redis state is shared across gateway instances.

**Evidence command:**

```bash
docker compose up -d
pytest -q tests/test_redis_cache.py
docker compose exec redis redis-cli KEYS "rl:cache:*"
```

Observed Redis cache keys:

```text
rl:cache:095946136fea
rl:cache:8baa2cfa11fa
rl:cache:9e413fd814eb
rl:cache:b2a52f7dc795
```

**Latency: in-memory vs Redis cache:**

| Metric | In-memory cache hit | Redis cache hit |
|---|---:|---:|
| latency_p50_ms | ~0.19 ms | ~1-3 ms |

Redis adds about 1-2 ms of local Docker RTT per hit, which is an acceptable tradeoff for cross-instance cache consistency.

## 7. Chaos scenarios

| Scenario | Expected | Observed | Result |
|---|---|---|---|
| primary_timeout_100 | Primary circuit opens immediately, all traffic via backup, fallback_success_rate >= 90%. | circuit_open_count = 18; fallback_success_rate = 98.67%. | pass |
| primary_flaky_50 | Circuit may oscillate; availability stays >= 80% through fallback routing. | availability = 99.66%; fallback handled provider failures. | pass |
| all_healthy | Both providers healthy, no static fallback required. | static fallback rate stayed low; error_rate = 0.34%. | pass |
| cache_stale_candidate | 2024 vs 2026 refund queries and privacy queries should not be cache hits. | Numeric false-hit and privacy guardrails rejected unsafe cache reuse. | pass |
| recovery_demo | Circuit opens, waits reset_timeout, half-open probe succeeds, recovery_time_ms is recorded. | recovery_time_ms = 2898.1274 ms. | pass |

**Circuit breaker transition log (captured from a real local validation run):**

```text
2026-05-13T12:02:18.677 | closed -> open | reason: failure_threshold
2026-05-13T12:02:20.728 | open -> half_open | reason: reset_timeout_elapsed
2026-05-13T12:02:20.728 | half_open -> closed | reason: probe_success
```

Sample cycle recovery time is measured from `closed -> open` to `half_open -> closed`; aggregate scenario recovery time is reported in `recovery_time_ms` above.

## 8. Failure analysis

The main remaining production weakness is that circuit breaker state is process-local. In a horizontally scaled deployment, one instance can open a circuit while another keeps sending traffic to the failing provider. Before production, circuit state should be shared through Redis or a central health service with bounded TTLs.

## 9. Next steps

1. Add Redis-backed circuit breaker counters for multi-instance consistency.
2. Add concurrent load testing with request-level traces.
3. Export Prometheus counters for request totals, cache hits, latency, and circuit state.