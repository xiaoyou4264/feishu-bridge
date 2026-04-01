"""Tests for src/dedup.py — DeduplicationCache."""
import pytest
from src.dedup import DeduplicationCache


class TestDeduplicationCache:
    def test_first_call_returns_false(self):
        """First call to is_duplicate returns False (not a duplicate)."""
        cache = DeduplicationCache()
        assert cache.is_duplicate("evt_001") is False

    def test_second_call_returns_true(self):
        """Second call to is_duplicate with same event_id returns True."""
        cache = DeduplicationCache()
        cache.is_duplicate("evt_001")
        assert cache.is_duplicate("evt_001") is True

    def test_different_event_id_returns_false(self):
        """is_duplicate with a different event_id returns False."""
        cache = DeduplicationCache()
        cache.is_duplicate("evt_001")
        assert cache.is_duplicate("evt_002") is False

    def test_cache_respects_max_size(self):
        """After inserting max_size+1 items, the cache size stays at max_size."""
        max_size = 3
        cache = DeduplicationCache(max_size=max_size)
        for i in range(max_size + 1):
            cache.is_duplicate(f"evt_{i:03d}")
        # Internal cache should not exceed max_size
        assert len(cache._cache) <= max_size

    def test_cache_eviction_fifo(self):
        """Oldest entry is evicted first when cache is at capacity (FIFO)."""
        cache = DeduplicationCache(max_size=2)
        # Fill to capacity
        cache.is_duplicate("first")
        cache.is_duplicate("second")
        # Insert one more — "first" (oldest) should be evicted; cache = {"second", "third"}
        cache.is_duplicate("third")
        # "first" should no longer be a duplicate (evicted)
        assert cache.is_duplicate("first") is False
        # "third" should still be a duplicate (not evicted yet)
        # Note: after re-adding "first", cache = {"third", "first"}, "second" was evicted
        assert cache.is_duplicate("third") is True
