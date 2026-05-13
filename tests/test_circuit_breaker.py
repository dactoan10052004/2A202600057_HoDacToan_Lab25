import time

import pytest

from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_circuit_opens_fails_fast_and_recovers() -> None:
    breaker = CircuitBreaker("primary", failure_threshold=2, reset_timeout_seconds=0.01)

    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    assert not breaker.allow_request()
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "never called")

    time.sleep(0.02)
    assert breaker.allow_request()
    assert breaker.state == CircuitState.HALF_OPEN

    breaker.record_success()

    assert breaker.state == CircuitState.CLOSED
    assert [entry["to"] for entry in breaker.transition_log] == ["open", "half_open", "closed"]


def test_half_open_failure_reopens() -> None:
    breaker = CircuitBreaker("primary", failure_threshold=1, reset_timeout_seconds=0.01)
    breaker.record_failure()
    time.sleep(0.02)
    assert breaker.allow_request()

    breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    assert breaker.transition_log[-1]["reason"] == "probe_failure"
