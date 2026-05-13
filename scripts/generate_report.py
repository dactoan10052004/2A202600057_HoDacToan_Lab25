from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def fmt(value: Any) -> str:
    if value is None:
        return "not observed"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def pct(value: Any) -> str:
    if not isinstance(value, int | float):
        return fmt(value)
    return f"{value * 100:.2f}%"


def redis_cache_keys() -> list[str]:
    try:
        import redis

        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        keys = sorted(str(key) for key in client.keys("rl:cache:*"))
        client.close()
        return keys
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()
    metrics = json.loads(Path(args.metrics).read_text())
    cache_comparison = metrics.get("cache_comparison", {})
    redis_keys = redis_cache_keys()

    lines = [
        "# Day 10 Reliability Final Report",
        "",
        "## 1. Architecture summary",
        "",
        "The gateway wraps unreliable LLM providers with cache lookup, per-provider circuit breakers, fallback routing, static fallback, and run metrics.",
        "",
        "```text",
        "User Request",
        "  -> ReliabilityGateway",
        "  -> ResponseCache or SharedRedisCache",
        "  -> CircuitBreaker(primary) -> Provider primary",
        "  -> CircuitBreaker(backup)  -> Provider backup",
        "  -> Static fallback when all providers fail",
        "```",
        "",
        "## 2. Configuration rationale",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        "| failure_threshold | 3 | Opens quickly during sustained failures without tripping on one transient error. |",
        "| reset_timeout_seconds | 2 | Short enough for lab recovery evidence while still showing OPEN fail-fast behavior. |",
        "| success_threshold | 1 | A single successful half-open probe closes the circuit for fast recovery. |",
        "| cache TTL seconds | 300 | Five minutes is a reasonable freshness window for FAQ-style responses. |",
        "| similarity_threshold | 0.92 | High threshold limits semantic false hits; exact matches still score 1.0. |",
        "| load_test requests | 100 | Enough repeated traffic to expose fallback, cache hits, and latency percentiles. |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability | >= 95% | {pct(metrics.get('availability'))} | {'yes' if metrics.get('availability', 0) >= 0.95 else 'no'} |",
        f"| Error rate | <= 5% | {pct(metrics.get('error_rate'))} | {'yes' if metrics.get('error_rate', 1) <= 0.05 else 'no'} |",
        f"| Latency P95 | < 2500 ms | {fmt(metrics.get('latency_p95_ms'))} | {'yes' if metrics.get('latency_p95_ms', 999999) < 2500 else 'no'} |",
        f"| Fallback success rate | >= 90% | {pct(metrics.get('fallback_success_rate'))} | {'yes' if metrics.get('fallback_success_rate', 0) >= 0.9 else 'no'} |",
        f"| Recovery time | < 5000 ms | {fmt(metrics.get('recovery_time_ms'))} | {'yes' if (metrics.get('recovery_time_ms') or 999999) < 5000 else 'no'} |",
        "",
        "## 4. Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    for key, value in metrics.items():
        if key in {"scenarios", "cache_comparison"}:
            continue
        lines.append(f"| {key} | {fmt(value)} |")

    lines += [
        "",
        "## 5. Cache comparison",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---:|",
    ]
    if cache_comparison:
        no_p50 = cache_comparison.get("without_cache_latency_p50_ms", 0.0)
        yes_p50 = cache_comparison.get("with_cache_latency_p50_ms", 0.0)
        no_p95 = cache_comparison.get("without_cache_latency_p95_ms", 0.0)
        yes_p95 = cache_comparison.get("with_cache_latency_p95_ms", 0.0)
        no_cost = cache_comparison.get("without_cache_estimated_cost", 0.0)
        yes_cost = cache_comparison.get("with_cache_estimated_cost", 0.0)
        no_hit = cache_comparison.get("without_cache_hit_rate", 0.0)
        yes_hit = cache_comparison.get("with_cache_hit_rate", 0.0)
        lines += [
            f"| latency_p50_ms | {fmt(no_p50)} | {fmt(yes_p50)} | {fmt(yes_p50 - no_p50)} |",
            f"| latency_p95_ms | {fmt(no_p95)} | {fmt(yes_p95)} | {fmt(yes_p95 - no_p95)} |",
            f"| estimated_cost | {fmt(no_cost)} | {fmt(yes_cost)} | {fmt(yes_cost - no_cost)} |",
            f"| cache_hit_rate | {pct(no_hit)} | {pct(yes_hit)} | {pct(yes_hit - no_hit)} |",
        ]
    else:
        lines.append("| cache comparison | not generated | not generated | not generated |")

    lines += [
        "",
        "## 6. Redis shared cache",
        "",
        "In-memory cache is per process, so horizontally scaled gateways would miss entries created by sibling instances. `SharedRedisCache` stores query/response hashes in Redis with TTL, so separate gateway instances can reuse the same safe cached responses.",
        "",
        "Evidence command:",
        "",
        "```bash",
        "docker compose up -d",
        "pytest -q tests/test_redis_cache.py",
        "docker compose exec redis redis-cli KEYS \"rl:cache:*\"",
        "```",
        "",
        "Observed Redis cache keys:",
        "",
        "```text",
        *(redis_keys if redis_keys else ["No rl:cache:* keys observed at report generation time."]),
        "```",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Status |",
        "|---|---|",
    ]
    for key, value in metrics.get("scenarios", {}).items():
        lines.append(f"| {key} | {value} |")

    lines += [
        "",
        "## 8. Failure analysis",
        "",
        "The main remaining production weakness is that circuit breaker state is process-local. In a horizontally scaled deployment, one instance can open a circuit while another keeps sending traffic to the failing provider. Before production, circuit state should be shared through Redis or a central health service with bounded TTLs.",
        "",
        "## 9. Next steps",
        "",
        "1. Add Redis-backed circuit breaker counters for multi-instance consistency.",
        "2. Add concurrent load testing with request-level traces.",
        "3. Export Prometheus counters for request totals, cache hits, latency, and circuit state.",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
