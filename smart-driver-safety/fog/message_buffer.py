"""Thread-safe bounded buffer with optional JSON-lines persistence (survives Fog restart)."""
import json
import os
import threading
from collections import OrderedDict


class MessageBuffer:
    def __init__(self, max_size: int = 10000, path: str | None = None):
        self.max_size = max_size
        self.path = path
        self._items: "OrderedDict[str, dict]" = OrderedDict()  # keyed by message_id => dedupe
        self._lock = threading.Lock()
        self.dropped = 0
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        ev = json.loads(line)
                        self._items[ev["data"]["message_id"]] = ev

    def __len__(self):
        return len(self._items)

    def contains(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._items

    def add(self, event: dict) -> bool:
        """Returns False if duplicate. Oldest event is dropped when full (counted in .dropped)."""
        mid = event["data"]["message_id"]
        with self._lock:
            if mid in self._items:
                return False
            if len(self._items) >= self.max_size:
                self._items.popitem(last=False)
                self.dropped += 1
            self._items[mid] = event
            self._persist()
            return True

    def peek(self, n: int) -> list[dict]:
        with self._lock:
            return list(self._items.values())[:n]

    def remove(self, message_ids: list[str]):
        with self._lock:
            for m in message_ids:
                self._items.pop(m, None)
            self._persist()

    def _persist(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for ev in self._items.values():
                fh.write(json.dumps(ev) + "\n")
        os.replace(tmp, self.path)
