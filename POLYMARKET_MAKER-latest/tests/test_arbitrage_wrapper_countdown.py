from pathlib import Path
import sys
import types

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

requests_stub = types.SimpleNamespace(
    get=lambda *args, **kwargs: None,
    post=lambda *args, **kwargs: None,
    Session=lambda *args, **kwargs: types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None),
    RequestException=Exception,
    Timeout=Exception,
)
sys.modules.setdefault("requests", requests_stub)
sys.modules.setdefault("websocket", types.SimpleNamespace())

import arbitrage_wrapper as wrapper


def test_countdown_absolute_ts_forwarded(monkeypatch):
    captured = {}

    def fake_run(params, interactive=False):
        captured["countdown"] = params.countdown
        captured["countdown_absolute_ts"] = params.countdown_absolute_ts

    monkeypatch.setattr(wrapper, "_run_main", fake_run)

    ts = 1_700_000_000
    wrapper.run_arbitrage(
        market_url="https://example.com/event",
        direction="YES",
        size=1,
        countdown_absolute_ts=ts,
    )

    assert captured["countdown"] is None
    assert captured["countdown_absolute_ts"] == ts


def test_mutual_exclusive_countdown(monkeypatch):
    def fake_run(params, interactive=False):
        provided = [
            name
            for name, value in (
                ("countdown", params.countdown),
                ("countdown_minutes_before", params.countdown_minutes_before),
                ("countdown_absolute_ts", params.countdown_absolute_ts),
            )
            if value is not None
        ]
        if len(provided) > 1:
            raise ValueError("countdown 参数只能三选一")

    monkeypatch.setattr(wrapper, "_run_main", fake_run)

    with pytest.raises(ValueError):
        wrapper.run_arbitrage(
            market_url="https://example.com/event",
            direction="NO",
            size=1,
            countdown=5,
            countdown_absolute_ts=1_700_000_000,
        )
