from __future__ import annotations

import builtins
from pathlib import Path
import sys
import types

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

requests_stub = types.SimpleNamespace(
    get=lambda *args, **kwargs: None,
    post=lambda *args, **kwargs: None,
    Session=lambda *args, **kwargs: types.SimpleNamespace(
        get=lambda *a, **k: None, post=lambda *a, **k: None
    ),
)
sys.modules.setdefault("requests", requests_stub)
sys.modules.setdefault("websocket", types.SimpleNamespace())

import arbitrage_wrapper as wrapper


_DEFAULT_META = {"end_ts": 1_700_000_000, "end_ts_precise": True, "timezone_hint": "UTC"}


def _setup_common_stubs(monkeypatch):
    monkeypatch.setattr(wrapper, "_apply_timezone_override_meta", lambda meta, tz: meta)
    monkeypatch.setattr(wrapper, "_should_offer_common_deadline_options", lambda meta: False)


# ---------------------------------------------------------------------------
# subquestion auto-selection
# ---------------------------------------------------------------------------


def test_event_with_single_subquestion_is_autopicked(monkeypatch):
    _setup_common_stubs(monkeypatch)

    chosen_titles: list[str] = []

    def fake_resolve(url: str):
        import Volatility_arbitrage_run as var

        markets = [
            {"title": "Only", "clobTokenIds": ["yes-id", "no-id"], "end_ts": 1_700_000_000},
        ]
        chosen = var._pick_market_subquestion(markets)
        chosen_titles.append(chosen.get("title", ""))
        return chosen["clobTokenIds"][0], chosen["clobTokenIds"][1], chosen.get("title", ""), dict(_DEFAULT_META)

    monkeypatch.setattr(wrapper, "_resolve_with_fallback", fake_resolve)

    captured_inputs: list[str] = []

    def fake_run_main():
        while True:
            try:
                captured_inputs.append(builtins.input())
            except EOFError:
                break

    monkeypatch.setattr(wrapper, "_run_main", fake_run_main)

    wrapper.run_arbitrage(market_source="https://example.com/event/slug", direction="YES", size=1)

    assert chosen_titles == ["Only"]
    # 第一个输入值应为传入的事件页 URL
    assert captured_inputs[0] == "https://example.com/event/slug"


def test_subquestion_choice_is_honored(monkeypatch):
    _setup_common_stubs(monkeypatch)

    markets = [
        {"title": "First", "clobTokenIds": ["y1", "n1"], "end_ts": 1_700_000_000},
        {"title": "Second", "clobTokenIds": ["y2", "n2"], "end_ts": 1_700_000_000},
    ]
    monkeypatch.setattr(wrapper, "_list_markets_under_event", lambda slug: markets)
    monkeypatch.setattr(wrapper, "_market_meta_from_obj", lambda obj: dict(_DEFAULT_META))
    monkeypatch.setattr(wrapper, "_fetch_market_by_slug", lambda slug: None)
    monkeypatch.setattr(wrapper, "resolve_token_ids", lambda url: (None, None, None, None))

    chosen_titles: list[str] = []
    monkeypatch.setattr(wrapper, "_run_main", lambda: None)

    orig_resolve_event = wrapper._resolve_event_with_choice

    def capture_resolve_event(event_slug: str, idx: int | None, direct: str | None):
        result = orig_resolve_event(event_slug, idx, direct)
        chosen_titles.append(markets[idx]["title"])
        return result

    monkeypatch.setattr(wrapper, "_resolve_event_with_choice", capture_resolve_event)

    wrapper.run_arbitrage(
        market_url="https://example.com/event/slug",
        direction="NO",
        size=None,
        subquestion_choice=1,
    )

    assert chosen_titles == ["Second"]


def test_out_of_range_subquestion_choice_is_rejected(monkeypatch):
    _setup_common_stubs(monkeypatch)

    monkeypatch.setattr(
        wrapper,
        "_list_markets_under_event",
        lambda slug: [{"title": "Only", "clobTokenIds": ["y1", "n1"], "end_ts": 1_700_000_000}],
    )
    monkeypatch.setattr(wrapper, "resolve_token_ids", lambda url: (None, None, None, None))
    monkeypatch.setattr(wrapper, "_fetch_market_by_slug", lambda slug: None)
    monkeypatch.setattr(wrapper, "_run_main", lambda: None)

    with pytest.raises(ValueError) as excinfo:
        wrapper.run_arbitrage(
            market_url="https://example.com/event/slug",
            direction="YES",
            size=None,
            subquestion_choice=5,
        )

    assert "子问题序号" in str(excinfo.value)


def test_extra_prompt_from_resolver_does_not_fail(monkeypatch):
    _setup_common_stubs(monkeypatch)

    call_count = 0

    def fake_resolve(url: str):
        nonlocal call_count
        # 模拟解析过程中多次触发 input()
        _ = builtins.input("first")
        _ = builtins.input("second")
        call_count += 1
        return "yes", "no", "title", dict(_DEFAULT_META)

    monkeypatch.setattr(wrapper, "_resolve_with_fallback", fake_resolve)
    monkeypatch.setattr(wrapper, "_run_main", lambda: None)

    wrapper.run_arbitrage(
        market_source="https://example.com/market/slug",
        direction="YES",
        size=1,
        subquestion_choice=2,
    )

    assert call_count == 1

