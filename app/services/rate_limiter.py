"""Small process-local sliding-window limiter for public and expensive endpoints."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


class SlidingWindowRateLimiter:
    """Thread-safe limiter suitable for a single Render web-service process."""

    def __init__(self) -> None:
        self._events: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, Optional[int]]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, math.ceil(window_seconds - (now - events[0])))
                return False, retry_after
            events.append(now)
            return True, None


rate_limiter = SlidingWindowRateLimiter()
