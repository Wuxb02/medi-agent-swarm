"""
熔断器 — 连续 N 次错误后拒绝新请求 T 秒

状态机: CLOSED → OPEN → HALF_OPEN → CLOSED
"""
import time
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class CircuitBreaker:
    """熔断器：连续 failure_threshold 次错误后断开 cooldown_seconds 秒

    使用示例:
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=30.0)
        if cb.is_open:
            raise CircuitBreakerOpenError("熔断器已打开")
        try:
            result = await do_llm_call()
            cb.record_success()
        except Exception:
            cb.record_failure()
            raise
    """

    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    half_open_max_calls: int = 1

    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _state: str = field(default="CLOSED", init=False)
    _half_open_count: int = field(default=0, init=False)

    @property
    def is_open(self) -> bool:
        """是否处于断开状态（拒绝新请求）"""
        if self._state == "CLOSED":
            return False
        if self._state == "OPEN":
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "HALF_OPEN"
                self._half_open_count = 0
                logger.info("CircuitBreaker: OPEN → HALF_OPEN")
                return False
            return True
        return False

    def record_success(self):
        """记录成功调用"""
        if self._state == "HALF_OPEN":
            self._half_open_count += 1
            if self._half_open_count >= self.half_open_max_calls:
                self._state = "CLOSED"
                self._failure_count = 0
                logger.info("CircuitBreaker: HALF_OPEN → CLOSED")
        elif self._state == "CLOSED":
            self._failure_count = 0

    def record_failure(self):
        """记录失败调用"""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._state in ("CLOSED", "HALF_OPEN"):
            if self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                logger.warning(
                    f"CircuitBreaker: → OPEN (failures={self._failure_count})"
                )


class CircuitBreakerOpenError(Exception):
    """熔断器打开时抛出的异常"""
    pass
