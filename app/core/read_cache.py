"""Bounded, process-local read cache with coalesced loads and mutation invalidation.

No persistent user data is stored here. Each worker has its own short TTL cache;
cross-worker freshness is bounded by that TTL.
"""

from collections import OrderedDict
from threading import Condition
from time import monotonic
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class ReadCache(Generic[T]):
    def __init__(self, ttl: float = 15, capacity: int = 128):
        self.ttl = ttl
        self.capacity = capacity
        self._items = OrderedDict()
        self._loading = set()
        self._condition = Condition()
        self._generation = 0

    def clear(self):
        with self._condition:
            self._items.clear()
            self._generation += 1

    def get_or_load(self, key: str, load: Callable[[], T]) -> tuple[T, bool]:
        with self._condition:
            while True:
                cached = self._items.get(key)
                if cached and cached[0] > monotonic():
                    self._items.move_to_end(key)
                    return cached[1], True
                if key not in self._loading:
                    self._loading.add(key)
                    generation = self._generation
                    break
                if not self._condition.wait_for(
                    lambda: key not in self._loading, timeout=10
                ):
                    raise TimeoutError(
                        "A read is still in progress. Please retry shortly."
                    )
        try:
            value = load()
            with self._condition:
                if generation == self._generation:
                    self._items[key] = (monotonic() + self.ttl, value)
                    self._items.move_to_end(key)
                    while len(self._items) > self.capacity:
                        self._items.popitem(last=False)
            return value, False
        finally:
            with self._condition:
                self._loading.discard(key)
                self._condition.notify_all()


dashboard_cache = ReadCache(ttl=15)
graph_cache = ReadCache(ttl=20)


def invalidate_read_caches(*_):
    dashboard_cache.clear()
    graph_cache.clear()
