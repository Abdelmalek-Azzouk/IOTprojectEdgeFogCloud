"""Store-and-forward logic, independent of FastAPI so it is unit-testable.

send_fn(events: list[dict]) -> dict   must raise on connectivity failure
"""
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable

from fog.message_buffer import MessageBuffer

log = logging.getLogger("fog.forwarder")


class CloudForwarder:
    def __init__(self, send_fn: Callable[[list[dict]], dict], buffer: MessageBuffer,
                 batch_size: int = 100, dedupe_window: int = 20000):
        self.send_fn = send_fn
        self.buffer = buffer
        self.batch_size = batch_size
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._flush_lock = threading.Lock()
        self.cloud_up = True
        self.last_error: str | None = None
        self.last_success_ts: float | None = None
        self.counters = {"received": 0, "duplicates_dropped": 0, "forwarded": 0,
                         "cloud_duplicates": 0, "cloud_rejected": 0, "send_failures": 0}
        self._dedupe_window = dedupe_window
        self._stop = threading.Event()

    # -- intake --
    def submit(self, event: dict) -> str:
        """Returns 'forwarded', 'buffered' or 'duplicate'."""
        mid = event["data"]["message_id"]
        self.counters["received"] += 1
        if mid in self._seen:
            self.counters["duplicates_dropped"] += 1
            return "duplicate"
        self._seen[mid] = None
        if len(self._seen) > self._dedupe_window:
            self._seen.popitem(last=False)
        self.buffer.add(event)
        self.flush()
        return "buffered" if self.buffer.contains(mid) else "forwarded"

    # -- delivery --
    def flush(self) -> int:
        """Try to deliver everything buffered. Stops at the first failure (cloud down)."""
        sent_total = 0
        with self._flush_lock:
            while True:
                batch = self.buffer.peek(self.batch_size)
                if not batch:
                    break
                try:
                    res = self.send_fn(batch)
                except Exception as exc:  # network error / 5xx => keep in buffer
                    self.cloud_up = False
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self.counters["send_failures"] += 1
                    log.warning("cloud unreachable, %d buffered: %s", len(self.buffer), self.last_error)
                    break
                self.buffer.remove([e["data"]["message_id"] for e in batch])
                self.cloud_up = True
                self.last_error = None
                self.last_success_ts = time.time()
                self.counters["forwarded"] += len(batch)
                self.counters["cloud_duplicates"] += res.get("duplicates", 0)
                self.counters["cloud_rejected"] += len(res.get("rejected", []))
                sent_total += len(batch)
        return sent_total

    # -- background retry --
    def start(self, interval_s: float = 2.0):
        def loop():
            while not self._stop.wait(interval_s):
                if len(self.buffer):
                    self.flush()
        threading.Thread(target=loop, daemon=True, name="fog-retry").start()

    def stop(self):
        self._stop.set()
