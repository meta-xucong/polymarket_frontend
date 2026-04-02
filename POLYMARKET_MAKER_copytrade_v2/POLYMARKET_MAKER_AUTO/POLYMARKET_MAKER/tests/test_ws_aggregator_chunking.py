import sys
import types
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _WebsocketStub:
    class WebSocketApp:  # pragma: no cover - 仅用于导入占位
        def __init__(self, *args, **kwargs):
            pass


sys.modules.setdefault("websocket", _WebsocketStub())

from Volatility_arbitrage_main_ws import WSAggregatorClient


class _FakeWS:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


def test_flush_pending_subscriptions_sends_subscribe_in_chunks():
    client = WSAggregatorClient(subscribe_chunk_size=2, subscribe_chunk_interval_sec=0.0)
    ws = _FakeWS()

    with client._lock:
        client._pending_subscribe.update({"a", "b", "c", "d", "e"})

    client._flush_pending_subscriptions(ws)

    # 5 个 token，chunk_size=2 -> 至少 3 条 subscribe 消息
    subscribe_msgs = [msg for msg in ws.sent if '"operation": "subscribe"' in msg]
    assert len(subscribe_msgs) == 3
    assert all('"initial_dump": true' in msg.lower() for msg in subscribe_msgs)


def test_flush_pending_subscriptions_sends_unsubscribe_in_chunks():
    client = WSAggregatorClient(subscribe_chunk_size=3, subscribe_chunk_interval_sec=0.0)
    ws = _FakeWS()

    with client._lock:
        client._pending_unsubscribe.update({"a", "b", "c", "d"})

    client._flush_pending_subscriptions(ws)

    unsubscribe_msgs = [msg for msg in ws.sent if '"operation": "unsubscribe"' in msg]
    assert len(unsubscribe_msgs) == 2


def test_on_message_pong_refreshes_last_event_ts():
    client = WSAggregatorClient()
    client._last_event_ts = 0.0

    before = time.monotonic()
    client._on_message(_FakeWS(), "PONG")

    assert client._last_event_ts >= before


def test_on_message_ping_replies_with_pong():
    client = WSAggregatorClient()
    ws = _FakeWS()

    client._on_message(ws, "PING")

    assert ws.sent[-1] == "PONG"


def test_resubscribe_marks_token_for_fresh_subscribe():
    client = WSAggregatorClient(subscribe_chunk_size=10, subscribe_chunk_interval_sec=0.0)
    ws = _FakeWS()

    with client._lock:
        client._subscribed_ids.add("alpha")

    changed = client.resubscribe(["alpha"])

    assert changed == 1
    with client._lock:
        assert "alpha" in client._pending_unsubscribe
        assert "alpha" in client._pending_subscribe
        assert "alpha" in client._force_initial_dump_ids

    client._flush_pending_subscriptions(ws)

    assert any('"operation": "unsubscribe"' in msg for msg in ws.sent)
    subscribe_msgs = [msg for msg in ws.sent if '"operation": "subscribe"' in msg]
    assert len(subscribe_msgs) == 1
    assert '"initial_dump": true' in subscribe_msgs[0].lower()
