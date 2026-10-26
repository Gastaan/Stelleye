"""Who is connected, which guides are online and free. Process-local, matching the in-memory channel layer.

Swap for Redis when running more than one server process.
"""
import threading
from collections import Counter

_lock = threading.Lock()
_online: set[int] = set()
_busy: set[int] = set()
_connections: Counter = Counter()


def connected(user_id: int) -> None:
    with _lock:
        _connections[user_id] += 1


def disconnected(user_id: int) -> int:
    """Returns how many open pages this user still has."""
    with _lock:
        _connections[user_id] -= 1
        remaining = max(_connections[user_id], 0)
        if remaining == 0:
            del _connections[user_id]
        return remaining


def set_online(user_id: int) -> None:
    with _lock:
        _online.add(user_id)


def set_offline(user_id: int) -> None:
    with _lock:
        _online.discard(user_id)
        _busy.discard(user_id)


def set_busy(user_id: int, busy: bool) -> None:
    with _lock:
        (_busy.add if busy else _busy.discard)(user_id)


def is_online(user_id: int) -> bool:
    with _lock:
        return user_id in _online


def available_guide_ids() -> set[int]:
    with _lock:
        return _online - _busy


def snapshot() -> tuple[set[int], set[int]]:
    """(online, busy) user ids."""
    with _lock:
        return set(_online), set(_busy)


def reset() -> None:
    """Test helper."""
    with _lock:
        _online.clear()
        _busy.clear()
        _connections.clear()
