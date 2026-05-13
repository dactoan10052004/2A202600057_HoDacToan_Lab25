from reliability_lab.chaos import calculate_recovery_time_ms, route_class, scenario_passed
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def test_route_class_extracts_detailed_reason() -> None:
    assert route_class("primary:primary:success") == "primary"
    assert route_class("fallback:backup:success") == "fallback"
    assert route_class("cache_hit:1.00") == "cache_hit"


def test_primary_timeout_requires_fallback_and_open_circuit() -> None:
    good = RunMetrics(
        total_requests=10,
        successful_requests=10,
        fallback_successes=10,
        circuit_open_count=1,
    )
    bad = RunMetrics(total_requests=10, successful_requests=10, fallback_successes=0)

    assert scenario_passed("primary_timeout_100", good)
    assert not scenario_passed("primary_timeout_100", bad)


def test_run_simulation_uses_concurrency_field() -> None:
    """run_simulation must respect concurrency > 1 and complete all requests."""
    from reliability_lab.chaos import run_simulation
    from reliability_lab.config import load_config

    config = load_config("configs/default.yaml")
    config.load_test.requests = 10
    config.load_test.concurrency = 2
    config.scenarios = []

    metrics = run_simulation(config, ["hello world"])
    assert metrics.total_requests == 10


def test_recovery_time_ms_is_measured_after_circuit_closes() -> None:
    """recovery_time_ms must be a real number when circuit opens then closes."""
    provider = FakeLLMProvider("primary", fail_rate=0.0, base_latency_ms=1, cost_per_1k_tokens=0.001)
    breaker = CircuitBreaker("primary", failure_threshold=2, reset_timeout_seconds=0.01)
    gateway = ReliabilityGateway([provider], {"primary": breaker})

    breaker.record_failure()
    breaker.record_failure()

    import time
    time.sleep(0.02)
    breaker.allow_request()
    breaker.record_success()

    recovery = calculate_recovery_time_ms(gateway)
    assert recovery is not None
    assert recovery > 0
