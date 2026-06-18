"""P2-15: CircuitBreaker 单元测试"""
import time
import pytest
from mediZJ.core.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    """测试熔断器状态机"""

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.is_open is False
        assert cb._state == "CLOSED"

    def test_open_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is True
        assert cb._state == "OPEN"

    def test_success_resets_counter(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

    def test_cooldown_transition_to_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        assert cb._state == "OPEN"
        time.sleep(0.02)
        assert cb.is_open is False
        assert cb._state == "HALF_OPEN"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.is_open  # 触发 HALF_OPEN 转换
        cb.record_success()
        assert cb._state == "CLOSED"
        assert cb._failure_count == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.is_open  # 触发 HALF_OPEN 转换
        cb.record_failure()
        assert cb._state == "OPEN"

    def test_is_open_returns_true_when_open_and_not_cooled(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=999)
        cb.record_failure()
        assert cb.is_open is True

    def test_not_open_during_closed(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False
