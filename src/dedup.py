"""Deduplication cache for Feishu event IDs."""
import time
from collections import OrderedDict


class DeduplicationCache:
    """
    Bounded LRU cache for event deduplication.

    Uses collections.OrderedDict for O(1) insertion, lookup, and FIFO eviction.
    Thread-safe for single-threaded asyncio event loop use — no locks needed.

    Args:
        max_size: Maximum number of event IDs to cache. Oldest entry is evicted
                  when capacity is exceeded. Default: 1000.
        ttl_seconds: Time-to-live in seconds for cached entries (informational;
                     eviction is size-based, not time-based in this implementation).
                     Default: 60.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 60) -> None:
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def is_duplicate(self, event_id: str) -> bool:
        """
        Check if event_id was seen recently.

        Returns True if event_id is a duplicate (already seen).
        Returns False and marks event_id as seen if it is new.
        Evicts the oldest entry when cache exceeds max_size.
        """
        if event_id in self._cache:
            return True

        # Evict oldest entry if at capacity
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)  # FIFO: remove first/oldest item

        self._cache[event_id] = time.monotonic()
        return False
