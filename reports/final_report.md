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

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Opens quickly during sustained failures without tripping on one transient error. |
| reset_timeout_seconds | 2 | Short enough for lab recovery evidence while still showing OPEN fail-fast behavior. |
| success_threshold | 1 | A single successful half-open probe closes the circuit for fast recovery. |
| cache TTL seconds | 300 | Five minutes is a reasonable freshness window for FAQ-style responses. |
| similarity_threshold | 0.92 | High threshold limits semantic false hits; exact matches still score 1.0. |
| load_test requests | 100 | Enough repeated traffic to expose fallback, cache hits, and latency percentiles. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 95% | 99.40% | yes |
| Error rate | <= 5% | 0.60% | yes |
| Latency P95 | < 2500 ms | 317.6500 | yes |
| Fallback success rate | >= 90% | 97.88% | yes |
| Recovery time | < 5000 ms | 4473.7117 | yes |

## 4. Metrics

| Metric | Value |
|---|---:|
| total_requests | 1000 |
| availability | 0.9940 |
| error_rate | 0.0060 |
| latency_p50_ms | 1.7500 |
| latency_p95_ms | 317.6500 |
| latency_p99_ms | 532.0100 |
| fallback_success_rate | 0.9788 |
| cache_hit_rate | 0.5540 |
| circuit_open_count | 5 |
| recovery_time_ms | 4473.7117 |
| estimated_cost | 0.0955 |
| estimated_cost_saved | 0.0062 |

## 5. Cache comparison

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 211.3003 | 0.1888 | -211.1115 |
| latency_p95_ms | 237.7336 | 231.5088 | -6.2248 |
| estimated_cost | 0.0017 | 0.0005 | -0.0013 |
| cache_hit_rate | 0.00% | 74.50% | 74.50% |

## 6. Redis shared cache

In-memory cache is per process, so horizontally scaled gateways would miss entries created by sibling instances. `SharedRedisCache` stores query/response hashes in Redis with TTL, so separate gateway instances can reuse the same safe cached responses.

Evidence command:

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

## 7. Chaos scenarios

| Scenario | Status |
|---|---|
| primary_timeout_100 | pass |
| primary_flaky_50 | pass |
| all_healthy | pass |
| cache_stale_candidate | pass |
| recovery_demo | pass |

## 8. Failure analysis

The main remaining production weakness is that circuit breaker state is process-local. In a horizontally scaled deployment, one instance can open a circuit while another keeps sending traffic to the failing provider. Before production, circuit state should be shared through Redis or a central health service with bounded TTLs.

## 9. Next steps

1. Add Redis-backed circuit breaker counters for multi-instance consistency.
2. Add concurrent load testing with request-level traces.
3. Export Prometheus counters for request totals, cache hits, latency, and circuit state.