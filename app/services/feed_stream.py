"""One process-local SSE fanout hub; durable feed reads never call providers.

The DB journal owns recovery. This bounded hub only accelerates delivery and
releases each polling transaction before waiting or serving a slow browser.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_HUB_LOCK = threading.Lock()
_FRESHNESS_KEYS = (
    'state', 'last_success_at', 'last_attempt_at', 'provider_delay',
    'poll_interval_seconds', 'match_date', 'error_code',
)


class StreamCapacityError(Exception):
    """Reserved streaming threads are already occupied."""


@dataclass(frozen=True)
class FeedEvent:
    event: str
    data: dict
    revision: int | None = None

    def encode(self):
        event_id = f'id: {self.revision}\n' if self.revision is not None else ''
        return f'{event_id}event: {self.event}\ndata: {json.dumps(self.data, separators=(",", ":"), allow_nan=False)}\n\n'


def _cursor(value):
    # Bound untrusted headers before integer conversion, including Python 3.10.
    if isinstance(value, bool):
        return None
    text = str(value) if value is not None else ''
    if not text.isascii() or not text.isdecimal() or len(text) > 20:
        return None
    return int(text)


class FeedSubscription:
    def __init__(self, hub, after_revision):
        self.hub = hub
        self.cursor = _cursor(after_revision)
        self.created_at = hub.monotonic()
        self.closed = False
        self._pending = deque()

    @property
    def pending_count(self):
        with self.hub._condition:
            return len(self._pending)

    def close(self):
        self.hub.unsubscribe(self)

    def next_event(self, timeout):
        with self.hub._condition:
            self.hub._condition.wait_for(
                lambda: self.closed or bool(self._pending), timeout=timeout,
            )
            return self._pending.popleft() if self._pending and not self.closed else None

    def iter_sse(self):
        try:
            # Flush headers promptly even when the database is temporarily down.
            yield 'retry: 3000\n\n'
            next_heartbeat = self.hub.monotonic() + self.hub.heartbeat_seconds
            deadline = self.created_at + self.hub.max_lifetime_seconds
            while not self.closed:
                now = self.hub.monotonic()
                if now >= deadline:
                    break
                if now >= next_heartbeat:
                    yield self.hub.heartbeat().encode()
                    next_heartbeat = self.hub.monotonic() + self.hub.heartbeat_seconds
                    continue
                event = self.next_event(min(next_heartbeat - now, deadline - now))
                if event is not None:
                    yield event.encode()
        finally:
            self.close()


class FeedStreamHub:
    def __init__(
        self, reader, *, app=None, poll_seconds=1, heartbeat_seconds=15,
        max_lifetime_seconds=300, max_clients=8, max_queue=100,
        monotonic=time.monotonic, start_thread=True,
    ):
        self.reader = reader
        self.app = app
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.max_lifetime_seconds = max_lifetime_seconds
        self.max_clients = max_clients
        self.max_queue = max_queue
        self.monotonic = monotonic
        self.start_thread = start_thread
        self._condition = threading.Condition()
        self._subscribers = set()
        self._thread = None
        self._closed = False
        self._latest = None
        self._available = False

    @property
    def client_count(self):
        with self._condition:
            return len(self._subscribers)

    def subscribe(self, after_revision=None):
        with self._condition:
            if self._closed or len(self._subscribers) >= self.max_clients:
                raise StreamCapacityError()
            subscriber = FeedSubscription(self, after_revision)
            self._subscribers.add(subscriber)
            if self.start_thread and self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name='nba-feed-db-reader', daemon=True,
                )
                self._thread.start()
            self._condition.notify_all()
            return subscriber

    def unsubscribe(self, subscriber):
        with self._condition:
            subscriber.closed = True
            subscriber._pending.clear()
            self._subscribers.discard(subscriber)
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            for subscriber in tuple(self._subscribers):
                self.unsubscribe(subscriber)
            self._condition.notify_all()

    def _run(self):
        next_poll = 0.0
        while True:
            with self._condition:
                while not self._closed:
                    if not self._subscribers:
                        next_poll = 0.0
                        self._condition.wait()
                    else:
                        remaining = next_poll - self.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
                if self._closed:
                    return
            self.poll_once()
            next_poll = self.monotonic() + self.poll_seconds

    def _reset(self, subscriber, snapshot):
        subscriber._pending.clear()
        subscriber._pending.append(FeedEvent('reset', snapshot, snapshot['revision']))
        subscriber.cursor = snapshot['revision']

    def poll_once(self):
        with self._condition:
            subscribers = tuple(self._subscribers)
            if not subscribers or self._closed:
                return
            cursors = [item.cursor for item in subscribers if item.cursor is not None]
            after_revision = min(cursors) if cursors else None
        try:
            with self.app.app_context() if self.app is not None else nullcontext():
                try:
                    result = self.reader(after_revision)
                finally:
                    if self.app is not None:
                        from app import db
                        db.session.remove()
            snapshot = result['snapshot']
            revision = snapshot['revision']
            events = result['events']
        except Exception as exc:
            # Never publish exception details, DSNs, or provider credentials.
            logger.warning('Shared basketball feed DB read unavailable (%s)', type(exc).__name__)
            with self._condition:
                self._available = False
                for subscriber in self._subscribers:
                    subscriber._pending.clear()
            return
        with self._condition:
            recovered = self._latest is not None and not self._available
            self._latest = snapshot
            self._available = True
            for subscriber in subscribers:
                if subscriber.closed:
                    continue
                if recovered or subscriber.cursor is None or subscriber.cursor > revision:
                    # An unavailable heartbeat clears browser rows. Restore
                    # the full view even if recovery has no new revision.
                    self._reset(subscriber, snapshot)
                elif result['reset']:
                    # A very old client must not erase another client's valid
                    # replay. Reset the oldest group, then read the next cursor.
                    if after_revision is None or subscriber.cursor <= after_revision:
                        self._reset(subscriber, snapshot)
                else:
                    pending = [event for event in events if event['revision'] > subscriber.cursor]
                    if len(subscriber._pending) + len(pending) > self.max_queue:
                        self._reset(subscriber, snapshot)
                    else:
                        for event in pending:
                            subscriber._pending.append(FeedEvent('snapshot', event['payload'], event['revision']))
                            subscriber.cursor = event['revision']
            self._condition.notify_all()

    def heartbeat(self):
        with self._condition:
            latest = self._latest or {}
            freshness = {key: latest.get(key) for key in _FRESHNESS_KEYS}
            if not self._available:
                freshness['state'] = 'unavailable'
            return FeedEvent('heartbeat', {
                'revision': latest.get('revision'),
                'freshness': freshness,
                'stream': {'state': 'connected' if self._available else 'unavailable'},
            })


def get_feed_hub(app):
    """Lazily create exactly one shared reader for this web application."""
    with _HUB_LOCK:
        hub = app.extensions.get('nba_feed_hub')
        if hub is None:
            from app.services.basketball_feed import read_feed_events
            hub = FeedStreamHub(read_feed_events, app=app)
            app.extensions['nba_feed_hub'] = hub
        return hub
