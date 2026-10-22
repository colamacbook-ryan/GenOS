from gh.chassis.breaker import CLOSED, HALF_OPEN, OPEN, BreakerConfig, CircuitBreaker


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make() -> tuple[CircuitBreaker, Clock, list[tuple[str, str]]]:
    clock = Clock()
    seen: list[tuple[str, str]] = []
    b = CircuitBreaker(BreakerConfig(failure_threshold=3, window_s=10, cooldown_s=30), clock=clock)
    b.on_transition = lambda old, new, _r: seen.append((old, new))
    return b, clock, seen


def test_opens_after_threshold_within_window() -> None:
    b, clock, seen = make()
    for _ in range(2):
        b.record_failure("x")
    assert b.state == CLOSED
    b.record_failure("x")
    assert b.state == OPEN and not b.allow()
    assert seen == [(CLOSED, OPEN)]


def test_failures_outside_window_do_not_count() -> None:
    b, clock, _ = make()
    b.record_failure()
    b.record_failure()
    clock.t = 11
    b.record_failure()
    assert b.state == CLOSED


def test_half_open_single_probe_then_close() -> None:
    b, clock, seen = make()
    for _ in range(3):
        b.record_failure()
    clock.t = 30
    assert b.allow()                 # lượt thử
    assert b.state == HALF_OPEN
    assert not b.allow()             # chỉ một lượt thử cùng lúc
    b.record_success()
    assert b.state == CLOSED and b.allow()
    assert seen == [(CLOSED, OPEN), (OPEN, HALF_OPEN), (HALF_OPEN, CLOSED)]


def test_half_open_failure_reopens() -> None:
    b, clock, _ = make()
    for _ in range(3):
        b.record_failure()
    clock.t = 31
    assert b.allow()
    b.record_failure("vẫn lỗi")
    assert b.state == OPEN and not b.allow()
    assert b.total_errors == 4 and b.last_error == "vẫn lỗi"
