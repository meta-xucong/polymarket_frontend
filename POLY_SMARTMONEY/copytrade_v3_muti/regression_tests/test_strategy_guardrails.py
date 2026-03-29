def test_strategy_keeps_skip_buy_latched_until_parent_reconciles(strategy_source):
    anchor = strategy_source.index("[BUY][GUARD][SKIP_BUY]")
    window = strategy_source[max(0, anchor - 200) : anchor + 3600]

    assert "skip_buy_hard_latched = bool(force_sell_only_on_startup)" in strategy_source
    assert "if has_position and skip_buy:" in strategy_source
    assert "skip_buy guard: latched resume path" in window
    assert "[POSITION_TRUTH][DIVERGENCE] parent expected position " in window
    assert window.find("if skip_buy_hard_latched:") < window.find("skip_buy = False")


def test_strategy_logs_position_truth_startup_context(strategy_source):
    assert "position_truth_context = run_cfg.get(\"position_truth_context\")" in strategy_source
    assert "[POSITION_TRUTH] startup context:" in strategy_source
    assert "expected_size={position_truth_context.get('expected_position_size')}" in strategy_source


def test_strategy_respects_startup_sell_only_switch(strategy_source):
    assert "force_sell_only_on_startup = bool(run_cfg.get(\"force_sell_only_on_startup\", False))" in strategy_source
    assert "[INIT] 已启用启动仅卖出模式：" in strategy_source
    assert "if force_sell_only_on_startup:" in strategy_source
