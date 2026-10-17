import time
import threading

class RateLimitError(Exception):
    pass

class ModelRateLimiter:
    def __init__(self, rpm: int = 1000, tpm: int = 100_000):
        self.rpm = rpm
        self.tpm = tpm
        self._lock = threading.Lock()
        self._requests = 0.0
        self._tokens = 0.0
        self._win_start = time.monotonic()
        self._minute = 60.0
        self._floor_tpm = 10_000

    def _refill(self, now):
        if now - self._win_start >= self._minute:
            self._win_start = now
            self._requests = 0.0
            self._tokens = 0.0

    def acquire(self, est_tokens: int):
        # A single request whose estimate exceeds the whole window can never be
        # admitted (the predicate stays false even after the window resets), which
        # previously hung an unattended run forever. Clamp the estimate: the real
        # output is bounded by max_tokens server-side regardless.
        tpm = max(1, int(self.tpm))
        est = max(1, min(int(est_tokens), tpm))
        deadline_windows = 2
        waited_windows = 0
        last_win = self._win_start
        while True:
            with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._requests + 1 <= self.rpm and self._tokens + est <= self.tpm:
                    self._requests += 1
                    self._tokens += est
                    return
                if self._win_start != last_win:
                    waited_windows += 1
                    last_win = self._win_start
                # Bounded fallback: never block an unattended run indefinitely.
                # After two full windows still blocked, admit and let upstream
                # 429 / bounded retry absorb an occasional overshoot.
                if waited_windows >= deadline_windows:
                    self._requests += 1
                    self._tokens += est
                    return
                wait = max(0.1, self._minute - (now - self._win_start))
            time.sleep(wait)

    def report_429(self):
        with self._lock:
            self.tpm = max(self._floor_tpm, int(self.tpm * 0.75))

    def report_success(self):
        with self._lock:
            pass
