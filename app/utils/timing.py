import time
from collections.abc import Callable


class Stopwatch:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def elapsed(self) -> float:
        return self._clock() - self._start

    def elapsed_ms(self) -> int:
        return int(self.elapsed() * 1000)


class Deadline:
    """A wall-clock budget for one request, shared by every retry."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        return self._end - self._clock()

    def expired(self) -> bool:
        return self.remaining() <= 0
