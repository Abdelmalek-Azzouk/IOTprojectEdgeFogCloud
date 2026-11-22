import uuid

from fog.forwarder import CloudForwarder
from fog.message_buffer import MessageBuffer


def ev(kind="prediction"):
    return {"type": kind, "data": {"message_id": str(uuid.uuid4()), "device_id": "edge_1"}}


class FakeCloud:
    def __init__(self):
        self.up = True
        self.received = []

    def __call__(self, events):
        if not self.up:
            raise ConnectionError("cloud down")
        self.received += events
        return {"stored": len(events), "duplicates": 0, "rejected": []}


def make(path=None):
    cloud = FakeCloud()
    return cloud, CloudForwarder(cloud, MessageBuffer(100, path), batch_size=3)


def test_forward_when_cloud_up():
    cloud, fw = make()
    assert fw.submit(ev()) == "forwarded"
    assert len(cloud.received) == 1 and len(fw.buffer) == 0


def test_buffer_when_cloud_down_then_sync_on_recovery():
    cloud, fw = make()
    cloud.up = False
    assert all(fw.submit(ev()) == "buffered" for _ in range(7))
    assert len(fw.buffer) == 7 and not fw.cloud_up and cloud.received == []
    cloud.up = True
    assert fw.flush() == 7          # multiple batches of 3
    assert len(fw.buffer) == 0 and fw.cloud_up and len(cloud.received) == 7


def test_order_preserved():
    cloud, fw = make()
    cloud.up = False
    events = [ev() for _ in range(5)]
    for e in events:
        fw.submit(e)
    cloud.up = True
    fw.flush()
    assert [e["data"]["message_id"] for e in cloud.received] == [e["data"]["message_id"] for e in events]


def test_duplicate_submission_dropped():
    cloud, fw = make()
    cloud.up = False
    e = ev()
    assert fw.submit(e) == "buffered"
    assert fw.submit(e) == "duplicate"
    assert len(fw.buffer) == 1 and fw.counters["duplicates_dropped"] == 1


def test_buffer_overflow_drops_oldest():
    b = MessageBuffer(max_size=3)
    evs = [ev() for _ in range(5)]
    for e in evs:
        b.add(e)
    assert len(b) == 3 and b.dropped == 2
    assert b.peek(1)[0]["data"]["message_id"] == evs[2]["data"]["message_id"]


def test_buffer_persists_across_restart(tmp_path):
    p = str(tmp_path / "buf.jsonl")
    cloud, fw = make(p)
    cloud.up = False
    e = ev()
    fw.submit(e)
    # "restart" Fog: new buffer object loads from disk
    cloud2, fw2 = make(p)
    assert len(fw2.buffer) == 1
    fw2.flush()
    assert cloud2.received[0]["data"]["message_id"] == e["data"]["message_id"]
