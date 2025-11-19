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


def test_parameters_are_forwarded(monkeypatch):
    captured = {}

    def fake_run(params, interactive=False):
        captured["params"] = params
        captured["interactive"] = interactive

    monkeypatch.setattr(wrapper, "_run_main", fake_run)

    wrapper.run_arbitrage(
        market_url="https://example.com/market/slug",
        direction="NO",
        size=2,
        subquestion_choice=1,
        deadline_option="3",
    )

    params = captured["params"]
    assert params.market_source == "https://example.com/market/slug"
    assert params.subquestion_choice == 1
    assert params.direction == "NO"
    assert params.size == 2
    assert params.deadline_option == "3"
    assert captured["interactive"] is False


def test_missing_market_source_is_rejected():
    with pytest.raises(ValueError):
        wrapper.run_arbitrage(direction="YES")


def test_conflicting_countdown_arguments(monkeypatch):
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
            raise ValueError(
                "countdown 参数只能三选一（countdown / countdown_minutes_before / countdown_absolute_ts），当前同时提供: "
                + ", ".join(provided)
            )

    monkeypatch.setattr(wrapper, "_run_main", fake_run)

    with pytest.raises(ValueError):
        wrapper.run_arbitrage(
            market_url="https://example.com/market/slug",
            countdown=5,
            countdown_minutes_before=10,
        )
