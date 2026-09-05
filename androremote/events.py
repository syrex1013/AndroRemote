#!/usr/bin/env python3
"""Minimal thread-safe pub/sub event bus shared by the C2 core and the web UI.

The C2 core publishes; subscribers (web UI SSE, plugins, ...) receive dict
events:  {"type": ..., "ts": ..., "data": {...}}.  Kept dependency-free and
free of rich/console so it can be imported from anywhere without cycles.
"""

import json
import threading
import time


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = {}       # queue_id -> (queue, types_filter)
        self._seq = 0

    def subscribe(self, types=None):
        """Register a subscriber. Returns (sub_id, queue.Queue)."""
        import queue as _q
        q = _q.Queue(maxsize=1000)
        with self._lock:
            self._seq += 1
            sid = self._seq
            self._subs[sid] = (q, set(types) if types else None)
        return sid, q

    def unsubscribe(self, sid):
        with self._lock:
            self._subs.pop(sid, None)

    def publish(self, etype, **data):
        evt = {"type": etype, "ts": time.time(), **data}
        with self._lock:
            subs = list(self._subs.values())
        for q, filt in subs:
            if filt is not None and etype not in filt:
                continue
            try:
                q.put_nowait(evt)
            except Exception:
                pass  # slow consumer: drop rather than block the C2 hot path

    def publish_json(self, etype, **data):
        """Publish with JSON-safe coercion of values."""
        safe = {}
        for k, v in data.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = str(v)
        self.publish(etype, **safe)


BUS = EventBus()
