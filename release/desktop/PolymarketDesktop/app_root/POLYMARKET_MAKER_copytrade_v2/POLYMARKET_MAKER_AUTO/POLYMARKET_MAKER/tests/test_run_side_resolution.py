from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 仅用于导入目标模块，不在此文件中验证 requests 行为
requests_stub = types.SimpleNamespace(RequestException=Exception, get=lambda *_, **__: None, post=lambda *_, **__: None)
sys.modules.setdefault("requests", requests_stub)

ws_stub = types.SimpleNamespace(get_client=lambda: object(), ws_watch_by_ids=lambda *_, **__: None)
rest_stub = types.SimpleNamespace(get_client=lambda: object())
price_watch_stub = types.SimpleNamespace(resolve_token_ids=lambda *_, **__: ("YES", "NO", "", {}))

sys.modules.setdefault("Volatility_arbitrage_main_ws", ws_stub)
sys.modules.setdefault("Volatility_arbitrage_main_rest", rest_stub)
sys.modules.setdefault("Volatility_arbitrage_price_watch", price_watch_stub)

import Volatility_arbitrage_run as module


def test_resolve_wallet_prefers_client_funder():
    client = types.SimpleNamespace(funder="0xabc")
    addr, origin = module._resolve_wallet_address(client)
    assert addr == "0xabc"
    assert origin == "client.funder"


def test_resolve_wallet_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("POLY_FUNDER", "0xfeed")
    client = types.SimpleNamespace()
    addr, origin = module._resolve_wallet_address(client)
    assert addr == "0xfeed"
    assert origin == "env:POLY_FUNDER"


def test_extract_positions_official_shape_only():
    assert module._extract_positions_from_data_api_response([]) == []
    assert module._extract_positions_from_data_api_response([{"asset": "1"}]) == [{"asset": "1"}]
    assert module._extract_positions_from_data_api_response({"data": []}) is None


def test_market_meta_reads_enddateiso_as_official_date_only_deadline():
    meta = module._market_meta_from_obj(
        {
            "slug": "sample-market",
            "question": "US forces enter Iran by March 31?",
            "endDateIso": "2026-03-31",
        }
    )

    assert meta["slug"] == "sample-market"
    assert meta.get("end_ts") is not None
    assert meta.get("end_ts_precise") is False


def test_date_only_official_deadline_does_not_allow_hard_countdown():
    meta = {"end_ts": 1774915200.0, "end_ts_precise": False}

    assert module._deadline_allows_hard_countdown(meta, None) is False
    assert module._deadline_allows_hard_countdown(meta, 1775000000.0) is True


def test_real_case_march_31_midnight_enddate_stays_date_only():
    meta = module._market_meta_from_obj(
        {
            "question": "US forces enter Iran by March 31?",
            "slug": "us-forces-enter-iran-by-march-31-222-191-243-517-878-439-519",
            "endDate": "2026-03-01T00:00:00Z",
            "endDateIso": "2026-03-01",
        }
    )

    assert meta.get("end_ts") is not None
    assert meta.get("end_ts_precise") is False
    assert module._deadline_allows_hard_countdown(meta, None) is False


def test_real_case_december_31_midnight_enddate_stays_date_only():
    meta = module._market_meta_from_obj(
        {
            "question": "US forces enter Iran by December 31?",
            "slug": "us-forces-enter-iran-by-december-31-573-642-385-371-179",
            "endDate": "2026-03-03T00:00:00Z",
            "endDateIso": "2026-03-03",
        }
    )

    assert meta.get("end_ts") is not None
    assert meta.get("end_ts_precise") is False
    assert module._deadline_allows_hard_countdown(meta, None) is False
