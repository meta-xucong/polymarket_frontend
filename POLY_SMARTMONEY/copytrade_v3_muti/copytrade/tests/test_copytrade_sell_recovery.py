from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from copytrade.copytrade_run import run_once


class FakeClient:
    def __init__(self, trades_by_account, positions_by_account=None):
        self.trades_by_account = trades_by_account
        self.positions_by_account = positions_by_account or {}

    def fetch_trades(self, account, start_time=None, page_size=500, max_pages=5):
        return list(self.trades_by_account.get(account, []))

    def fetch_positions(self, account, page_size=500, max_pages=5, size_threshold=0.0):
        return list(self.positions_by_account.get(account, []))


def _trade(token_id: str, side: str, ts: datetime):
    return SimpleNamespace(
        side=side,
        size=10.0,
        raw={"tokenId": token_id, "side": side},
        timestamp=ts,
    )


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_sell_before_buy_is_deferred_then_promoted(tmp_path: Path):
    account = "0xabc"
    token_id = "token-1"
    t0 = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
    logger = logging.getLogger("test_copytrade_sell_recovery")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    config = {
        "targets": [{"account": account, "enabled": True, "min_size": 0}],
        "initial_lookback_sec": 0,
    }

    state_path = tmp_path / "copytrade_state.json"
    state_path.write_text(
        json.dumps(
            {
                "targets": {
                    account: {
                        "last_timestamp_ms": int((t0 - timedelta(minutes=1)).timestamp() * 1000),
                        "updated_at": t0.isoformat().replace("+00:00", "Z"),
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    run_once(
        config,
        base_dir=tmp_path,
        client=FakeClient({account: [_trade(token_id, "SELL", t0)]}),
        logger=logger,
    )

    sell_payload = _read_json(tmp_path / "copytrade_sell_signals.json")
    assert len(sell_payload["sell_tokens"]) == 1
    assert sell_payload["sell_tokens"][0]["token_id"] == token_id
    assert sell_payload["sell_tokens"][0]["status"] == "deferred_wait_buy_introduction"
    assert sell_payload["sell_tokens"][0]["introduced_by_buy"] is False

    token_payload = _read_json(tmp_path / "tokens_from_copytrade.json")
    assert token_payload["tokens"] == []

    t1 = t0 + timedelta(minutes=1)
    run_once(
        config,
        base_dir=tmp_path,
        client=FakeClient({account: [_trade(token_id, "BUY", t1)]}),
        logger=logger,
    )

    sell_payload = _read_json(tmp_path / "copytrade_sell_signals.json")
    assert len(sell_payload["sell_tokens"]) == 1
    assert sell_payload["sell_tokens"][0]["token_id"] == token_id
    assert sell_payload["sell_tokens"][0]["status"] == "pending"
    assert sell_payload["sell_tokens"][0]["introduced_by_buy"] is True

    token_payload = _read_json(tmp_path / "tokens_from_copytrade.json")
    assert len(token_payload["tokens"]) == 1
    assert token_payload["tokens"][0]["token_id"] == token_id
    assert token_payload["tokens"][0]["introduced_by_buy"] is True


def test_init_position_seed_allows_sell_without_historical_buy(tmp_path: Path):
    account = "0xseed"
    token_id = "token-seeded"
    t0 = datetime.now(timezone.utc)
    logger = logging.getLogger("test_copytrade_seed_positions")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False

    config = {
        "targets": [{"account": account, "enabled": True, "min_size": 5}],
        "initial_lookback_sec": 3600,
    }

    run_once(
        config,
        base_dir=tmp_path,
        client=FakeClient(
            {account: [_trade(token_id, "SELL", t0)]},
            {account: [{"asset": token_id, "size": "10"}]},
        ),
        logger=logger,
    )

    token_payload = _read_json(tmp_path / "tokens_from_copytrade.json")
    assert len(token_payload["tokens"]) == 1
    assert token_payload["tokens"][0]["token_id"] == token_id
    assert token_payload["tokens"][0]["introduced_by_buy"] is True

    sell_payload = _read_json(tmp_path / "copytrade_sell_signals.json")
    assert len(sell_payload["sell_tokens"]) == 1
    assert sell_payload["sell_tokens"][0]["token_id"] == token_id
    assert sell_payload["sell_tokens"][0]["status"] == "pending"
    assert sell_payload["sell_tokens"][0]["introduced_by_buy"] is True
