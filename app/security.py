"""Bounded, thread-safe attempt limits shared by login and sensitive settings."""
import math
import time
from threading import RLock


class AttemptLimiter:
    def __init__(self, limit=5, window=900, capacity=1024):
        self.limit, self.window, self.capacity = limit, window, capacity
        self.attempts = {}
        self.lock = RLock()

    def consume(self, key):
        """Reserve an attempt before password hashing; return retry delay or zero."""
        now = time.monotonic()
        with self.lock:
            self.attempts = {k: [stamp for stamp in stamps if now-stamp < self.window]
                             for k, stamps in self.attempts.items() if stamps and now-stamps[-1] < self.window}
            stamps = self.attempts.get(key, [])
            if len(stamps) >= self.limit:
                return max(1, math.ceil(stamps[0]+self.window-now))
            if key not in self.attempts and len(self.attempts) >= self.capacity:
                return self.window
            self.attempts.setdefault(key, []).append(now)
            return 0

    def clear(self, key):
        with self.lock:
            self.attempts.pop(key, None)


class PasswordConfirmationError(ValueError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after
