import sys
import types
from pathlib import Path


class _RequestException(Exception):
    pass


class _Timeout(_RequestException):
    pass


class _HTTPError(_RequestException):
    pass


def _default_get(*args, **kwargs):  # pragma: no cover - defensive stub
    raise RuntimeError("requests stub should be patched in tests")


requests_stub = types.SimpleNamespace(
    RequestException=_RequestException,
    Timeout=_Timeout,
    HTTPError=_HTTPError,
    get=_default_get,
)

sys.modules["requests"] = requests_stub

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _WebsocketStub(types.SimpleNamespace):
    def WebSocketApp(self, *args, **kwargs):  # pragma: no cover - defensive stub
        raise RuntimeError("websocket stub should not be used in tests")


sys.modules["websocket"] = _WebsocketStub()

from runtime_position_truth import (
    POSITION_TRUTH_ACTIONABLE,
    POSITION_TRUTH_DUST,
    POSITION_TRUTH_ZERO,
    classify_position_truth,
    extract_market_min_order_size,
)
from Volatility_arbitrage_run import _merge_remote_position_size


def test_classify_position_truth_respects_dust_and_actionable_boundary():
    assert classify_position_truth(0.0, market_min_order_size=5.0) == POSITION_TRUTH_ZERO
    assert classify_position_truth(0.000464, market_min_order_size=5.0) == POSITION_TRUTH_DUST
    assert classify_position_truth(4.999, market_min_order_size=5.0) == POSITION_TRUTH_DUST
    assert classify_position_truth(5.0, market_min_order_size=5.0) == POSITION_TRUTH_ACTIONABLE


def test_extract_market_min_order_size_reads_nested_payload():
    payload = {
        "market": {
            "params": {
                "minOrderSize": "7.5",
            }
        }
    }
    assert extract_market_min_order_size(payload) == 7.5


def test_merge_remote_position_size_discards_dust_remote_residue():
    new_size, changed = _merge_remote_position_size(
        current_size=6.0,
        remote_size=0.000464,
        dust_floor=5.0,
    )
    assert new_size is None
    assert changed is True


def test_merge_remote_position_size_keeps_actionable_remote_position():
    new_size, changed = _merge_remote_position_size(
        current_size=None,
        remote_size=6.0,
        dust_floor=5.0,
    )
    assert new_size == 6.0
    assert changed is True
