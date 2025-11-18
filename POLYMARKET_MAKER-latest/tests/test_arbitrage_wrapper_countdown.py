from __future__ import annotations

import builtins
from datetime import datetime, timezone
from pathlib import Path
import sys
import types

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

requests_stub = types.SimpleNamespace(
    get=lambda *args, **kwargs: None,
    post=lambda *args, **kwargs: None,
    Session=lambda *args, **kwargs: types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None),
)
sys.modules.setdefault("requests", requests_stub)
sys.modules.setdefault("websocket", types.SimpleNamespace())

import arbitrage_wrapper as wrapper


_DEFAULT_META = {
    "end_ts": 1_700_000_000,
    "end_ts_precise": True,
    "timezone_hint": "UTC",
}


def _setup_common_stubs(monkeypatch):
    monkeypatch.setattr(
        wrapper, "_resolve_with_fallback", lambda url: ("yes", "no", "t", dict(_DEFAULT_META))
    )
    monkeypatch.setattr(wrapper, "_apply_timezone_override_meta", lambda meta, tz: meta)
    monkeypatch.setattr(wrapper, "_should_offer_common_deadline_options", lambda meta: False)


# ---------------------------------------------------------------------------
# countdown_absolute_ts
# ---------------------------------------------------------------------------

def test_countdown_absolute_ts_is_coerced_to_iso(monkeypatch):
    _setup_common_stubs(monkeypatch)

    captured_inputs: list[str] = []

    def fake_run_main():
        while True:
            try:
                captured_inputs.append(builtins.input())
            except EOFError:
                break

    monkeypatch.setattr(wrapper, "_run_main", fake_run_main)

    ts = 1_700_000_000
    wrapper.run_arbitrage(
        market_url="https://example.com/event",
        direction="YES",
        size=1,
        countdown_absolute_ts=ts,
    )

    expected_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    assert captured_inputs[-1] == expected_iso


# ---------------------------------------------------------------------------
# mutual exclusivity
# ---------------------------------------------------------------------------

def test_only_one_countdown_value_is_allowed(monkeypatch):
    _setup_common_stubs(monkeypatch)
    monkeypatch.setattr(wrapper, "_run_main", lambda: None)

    with pytest.raises(ValueError) as excinfo:
        wrapper.run_arbitrage(
            market_url="https://example.com/event",
            direction="NO",
            size=1,
            countdown=5,
            countdown_absolute_ts=1_700_000_000,
        )

    assert "三选一" in str(excinfo.value)
