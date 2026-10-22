"""Circuit breaker theo cửa sổ thời gian: closed → open → half_open → closed (ARCHITECTURE §6.4)."""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


@dataclass
class BreakerConfig:
    failure_threshold: int = 5       # số lỗi trong cửa sổ để mở mạch
    window_s: float = 60.0
    cooldown_s: float = 60.0         # thời gian nghỉ trước khi nửa mở


@dataclass
class CircuitBreaker:
    config: BreakerConfig = field(default_factory=BreakerConfig)
    clock: Callable[[], float] = time.monotonic
    state: str = CLOSED
    opened_at: float = 0.0
    total_errors: int = 0
    last_error: str | None = None
    _failures: deque[float] = field(default_factory=deque)
    _probe_in_flight: bool = False
    on_transition: Callable[[str, str, str | None], None] | None = None

    def _set(self, new: str, reason: str | None = None) -> None:
        old, self.state = self.state, new
        if old != new and self.on_transition:
            self.on_transition(old, new, reason)

    def allow(self) -> bool:
        """True nếu được gọi. Ở half_open chỉ cho đúng một lượt thử."""
        if self.state == CLOSED:
            return True
        if self.state == OPEN:
            if self.clock() - self.opened_at >= self.config.cooldown_s:
                self._set(HALF_OPEN, "hết thời gian nghỉ")
            else:
                return False
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._probe_in_flight = False
        self._failures.clear()
        if self.state != CLOSED:
            self._set(CLOSED, "lượt thử thành công")

    def record_failure(self, error: str | None = None) -> None:
        now = self.clock()
        self.total_errors += 1
        self.last_error = error
        if self.state == HALF_OPEN:
            self._probe_in_flight = False
            self.opened_at = now
            self._set(OPEN, f"lượt thử thất bại: {error}")
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.config.window_s:
            self._failures.popleft()
        if len(self._failures) >= self.config.failure_threshold:
            self.opened_at = now
            self._set(OPEN, f"{len(self._failures)} lỗi trong {self.config.window_s:.0f}s: {error}")

    def cooldown_elapsed(self) -> bool:
        return self.clock() - self.opened_at >= self.config.cooldown_s

    @property
    def recent_failures(self) -> int:
        now = self.clock()
        return sum(1 for t in self._failures if now - t <= self.config.window_s)

    def reset(self) -> None:
        self._failures.clear()
        self._probe_in_flight = False
        self._set(CLOSED, "đặt lại thủ công")
